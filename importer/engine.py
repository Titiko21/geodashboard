"""
Moteur d'import générique de couches géospatiales.

Pipeline, identique quel que soit le format (GeoJSON / Shapefile /
GeoPackage — tout ce que lit GDAL/OGR) :

    fichier ──> lecture OGR ──> mapping colonnes → entrées logiques
            ──> reprojection WGS84 ──> normalisation géométrique (cible)
            ──> validation (cible) ──> upsert idempotent par clé stable

Le moteur ne connaît AUCUN modèle : tout ce qui est spécifique à une table
vit dans une TargetSpec (cf. registry.py). Le fichier de mapping (JSON)
fait le pont entre les colonnes réelles du fichier et les entrées logiques
de la cible :

    {
      "target": "commune",
      "columns": { "name": ["NOMS", "NOM", "name"] }
    }

`run_import(..., dry_run=True)` prévisualise sans rien écrire.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction

from .registry import get_target


class ImporterError(Exception):
    """Erreur d'import remontée à l'appelant (fichier, mapping, couche…)."""


# ── Mapping ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Mapping:
    target: str
    columns: dict  # entrée logique -> tuple de colonnes candidates


def load_mapping(path):
    """Charge et valide un fichier de mapping JSON → Mapping."""
    path = Path(path)
    if not path.exists():
        raise ImporterError(f"Fichier de mapping introuvable : {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ImporterError(f"Mapping illisible ({path}) : {exc}")

    target = data.get("target")
    if not target or not isinstance(target, str):
        raise ImporterError(f"Mapping {path} : clé « target » (str) requise.")

    raw_columns = data.get("columns")
    if not isinstance(raw_columns, dict) or not raw_columns:
        raise ImporterError(
            f"Mapping {path} : clé « columns » requise "
            "(dict entrée logique → colonne(s) du fichier)."
        )

    columns = {}
    for logical, cand in raw_columns.items():
        if isinstance(cand, str):
            cand = [cand]
        if (not isinstance(cand, list) or not cand
                or not all(isinstance(c, str) for c in cand)):
            raise ImporterError(
                f"Mapping {path} : « columns.{logical} » doit être une "
                "colonne (str) ou une liste de colonnes candidates."
            )
        columns[logical] = tuple(cand)

    return Mapping(target=target, columns=columns)


# ── Rapport ─────────────────────────────────────────────────────────────────

@dataclass
class ImportReport:
    dry_run: bool = False
    created: int = 0
    updated: int = 0
    skipped: list = field(default_factory=list)  # [(label, raison), …]

    @property
    def total_written(self):
        return self.created + self.updated


# ── Helpers ─────────────────────────────────────────────────────────────────

def resolve_column(layer_fields, candidates):
    """Première colonne du fichier correspondant (insensible à la casse)."""
    lower = {f.lower(): f for f in layer_fields}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _open_layer(file_path, layer_name=None):
    """Ouvre le fichier via OGR et sélectionne la couche."""
    from django.contrib.gis.gdal import DataSource

    path = Path(file_path)
    if not path.exists():
        raise ImporterError(f"Fichier introuvable : {path}")
    try:
        ds = DataSource(str(path))
    except Exception as exc:
        raise ImporterError(f"Lecture impossible ({path.name}) : {exc}")
    if len(ds) == 0:
        raise ImporterError(f"{path.name} : aucune couche détectée.")

    if layer_name:
        for layer in ds:
            if str(layer.name) == layer_name:
                return ds, layer
        names = ", ".join(str(l.name) for l in ds)
        raise ImporterError(
            f"Couche « {layer_name} » absente de {path.name}. "
            f"Couches disponibles : {names}."
        )
    return ds, ds[0]


def list_layers(file_path):
    """[(nom_couche, nb_features, [colonnes]), …] — pour l'exploration."""
    ds, _ = _open_layer(file_path)
    return [(str(l.name), len(l), list(l.fields)) for l in ds]


def _feature_geos(feat):
    """Géométrie GEOS en WGS84 d'une feature OGR, ou None si absente/invalide."""
    try:
        geom = feat.geom
    except Exception:
        return None
    if geom is None:
        return None
    if geom.srid and geom.srid != 4326:
        geom = geom.transform(4326, clone=True)
    try:
        geos = geom.geos
    except Exception:
        return None
    if geos is None or geos.empty:
        return None
    if not geos.srid:
        # Pas de SRS déclaré (ex. .shp sans .prj) : on suppose du WGS84.
        geos.srid = 4326
    return geos


# ── Import ──────────────────────────────────────────────────────────────────

def run_import(file_path, mapping_path, layer_name=None, dry_run=False,
               log=lambda msg: None):
    """
    Exécute (ou prévisualise) l'import d'un fichier géospatial.

    Renvoie un ImportReport. Lève ImporterError pour toute erreur de
    configuration (fichier, mapping, colonnes, cible).
    """
    mapping = load_mapping(mapping_path)
    try:
        target = get_target(mapping.target)
    except KeyError as exc:
        raise ImporterError(str(exc.args[0]))

    ds, layer = _open_layer(file_path, layer_name)
    layer_fields = list(layer.fields)
    log(f"Couche « {layer.name} » : {len(layer)} feature(s), "
        f"colonnes {layer_fields}")

    # Résolution des colonnes : entrée logique → colonne réelle du fichier.
    resolved = {}
    for logical, candidates in mapping.columns.items():
        col = resolve_column(layer_fields, candidates)
        if col is None and logical in target.required_inputs:
            raise ImporterError(
                f"Aucune colonne trouvée pour « {logical} » "
                f"(candidates : {', '.join(candidates)}) — "
                f"colonnes du fichier : {layer_fields}."
            )
        resolved[logical] = col
    log("Mapping colonnes : "
        + ", ".join(f"{k} ← {v or '∅'}" for k, v in sorted(resolved.items())))

    missing_required = [
        i for i in target.required_inputs if i not in resolved
    ]
    if missing_required:
        raise ImporterError(
            f"Entrée(s) requise(s) absente(s) du mapping : "
            f"{', '.join(missing_required)}."
        )

    report = ImportReport(dry_run=dry_run)

    # ── 1. Lecture + validation des features ────────────────────────────────
    prepared = []
    for i, feat in enumerate(layer):
        inputs = {}
        for logical, col in resolved.items():
            val = feat.get(col) if col else None
            inputs[logical] = str(val).strip() if val is not None else ""
        label = inputs.get("name") or f"feature #{i}"

        empty = [l for l in target.required_inputs if not inputs.get(l)]
        if empty:
            report.skipped.append((label, f"valeur vide pour : {', '.join(empty)}"))
            continue

        if target.accept is not None:
            ok, reason = target.accept(inputs)
            if not ok:
                report.skipped.append((label, reason))
                continue

        geos = _feature_geos(feat)
        if geos is None:
            report.skipped.append((label, "géométrie absente ou invalide"))
            continue
        normalized = target.normalize_geometry(geos)
        if normalized is None:
            report.skipped.append(
                (label, f"géométrie {geos.geom_type} non {target.geometry_label}")
            )
            continue

        key = target.build_key(inputs)
        defaults = target.build_defaults(inputs)
        defaults[target.geometry_field] = normalized
        prepared.append((label, key, defaults))

    # ── 2. Écriture (ou aperçu) ─────────────────────────────────────────────
    Model = target.model()
    existing = set(
        Model.objects.values_list(target.key_field, flat=True)
    )

    if dry_run:
        for label, key, _ in prepared:
            key_value = key[target.key_field]
            if key_value in existing:
                report.updated += 1
                log(f"  [dry-run] ~ {label} → {key_value} (mise à jour)")
            else:
                report.created += 1
                log(f"  [dry-run] + {label} → {key_value} (création)")
            existing.add(key_value)  # doublon intra-fichier compté en MAJ
    else:
        with transaction.atomic():
            for label, key, defaults in prepared:
                _, created = Model.objects.update_or_create(**key, defaults=defaults)
                if created:
                    report.created += 1
                    log(f"  + {label} ({key[target.key_field]})")
                else:
                    report.updated += 1
                    log(f"  ~ {label} ({key[target.key_field]}) — mise à jour")

    for label, reason in report.skipped:
        log(f"  ! {label} — ignorée : {reason}")

    return report
