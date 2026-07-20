"""
Registre des cibles d'import.

Une « cible » (TargetSpec) décrit comment les features d'un fichier
géospatial deviennent des lignes d'un modèle Django :

  - quel modèle, quel champ géométrique, quelle clé d'upsert ;
  - quelles entrées logiques sont attendues du fichier (ex. "name") —
    le fichier de mapping fait le pont entre ces entrées logiques et les
    vraies colonnes du fichier ;
  - comment normaliser la géométrie (polygone, ligne…) ;
  - comment construire la clé stable et les valeurs du modèle ;
  - un filtre d'acceptation optionnel (ensemble fermé, validation métier).

Phase A : une seule cible, « commune ». Les cibles routes/drainage/terrain
s'ajouteront ici au fil des phases (B, C…), sans toucher au moteur.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from admin_divisions._geo_utils import to_multipolygon
from admin_divisions.communes import canonical_name, commune_code, is_known_commune


@dataclass(frozen=True)
class TargetSpec:
    name: str                       # identifiant de la cible ("commune")
    app_model: str                  # "app_label.ModelName"
    key_field: str                  # champ d'upsert (unique) du modèle
    required_inputs: tuple          # entrées logiques obligatoires ("name", …)
    geometry_field: str             # champ géométrique du modèle
    geometry_label: str             # pour les messages ("polygonale", …)
    normalize_geometry: Callable    # GEOSGeometry -> GEOSGeometry | None
    build_key: Callable             # inputs dict -> {key_field: valeur}
    build_defaults: Callable        # inputs dict -> defaults du modèle
    accept: Optional[Callable] = None  # inputs dict -> (bool, raison)
    # Travail de suite déclenché APRÈS une écriture réussie (jamais en
    # dry-run). Signature : (written_keys: list, log: Callable) -> None.
    # Réservé aux traitements par lot — le moteur ne l'appelle que si
    # l'appelant l'autorise (`run_hooks`), car une passerelle HTTP ne peut
    # pas se permettre plusieurs minutes de calcul.
    after_import: Optional[Callable] = None

    def model(self):
        from django.apps import apps
        return apps.get_model(self.app_model)


_TARGETS = {}


def register(spec: TargetSpec):
    _TARGETS[spec.name] = spec
    return spec


def get_target(name):
    if name not in _TARGETS:
        available = ", ".join(sorted(_TARGETS)) or "(aucune)"
        raise KeyError(
            f"Cible d'import inconnue : « {name} ». Disponibles : {available}."
        )
    return _TARGETS[name]


def available_targets():
    return sorted(_TARGETS)


# ── Cible « commune » ───────────────────────────────────────────────────────

def _commune_accept(inputs):
    if not is_known_commune(inputs["name"]):
        return False, f"commune non reconnue : « {inputs['name']} »"
    return True, ""


# ── Cible « flood_event » — historique des inondations observées ───────────

def _parse_event_date(raw):
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _event_code(inputs):
    from admin_divisions._geo_utils import normalize_name
    slug = normalize_name(inputs["name"]).upper().replace(" ", "-")[:40]
    date = (inputs.get("date") or "").replace("/", "-")
    return f"EVT-{slug}" + (f"-{date}" if date else "")


def _recompute_after_events(written_keys, log):
    """
    Un relevé importé impose un PLANCHER au score de sa commune (scoring v3).
    Sans ce recalcul, la carte afficherait des points d'inondation que les
    scores communaux ignorent — deux lectures contradictoires à l'écran.

    Recalcule les SEULES communes recoupant les relevés écrits : ~15 s
    chacune contre ~3,5 min pour les 14 (mesuré le 2026-07-20).
    """
    from flood.models import FloodEvent
    from flood.susceptibility import communes_intersecting, recompute_communes

    geoms = list(
        FloodEvent.objects.filter(code__in=written_keys)
        .values_list("geom", flat=True)
    )
    communes = communes_intersecting(geoms)
    if not communes.exists():
        log("Aucune commune recoupée par ces relevés — scores inchangés.")
        return

    noms = ", ".join(communes.order_by("name").values_list("name", flat=True))
    log(f"Recalcul de la susceptibilité — commune(s) concernée(s) : {noms}")
    ok, failed = recompute_communes(communes, log=log)
    log(f"{ok} commune(s) recalculée(s), {len(failed)} échec(s).")
    if failed:
        log("⚠ Échecs (scores inchangés, à relancer) : " + ", ".join(failed))


register(TargetSpec(
    name="flood_event",
    app_model="flood.FloodEvent",
    key_field="code",
    required_inputs=("name",),
    geometry_field="geom",
    geometry_label="ponctuelle ou polygonale",
    normalize_geometry=lambda g: g,   # point OU polygone acceptés
    build_key=lambda inputs: {"code": _event_code(inputs)},
    build_defaults=lambda inputs: {
        "name":   inputs["name"],
        "date":   _parse_event_date(inputs.get("date")),
        "source": inputs.get("source") or "",
    },
    after_import=_recompute_after_events,
))


register(TargetSpec(
    name="commune",
    app_model="admin_divisions.Commune",
    key_field="code",
    required_inputs=("name",),
    geometry_field="geom",
    geometry_label="polygonale",
    normalize_geometry=to_multipolygon,
    build_key=lambda inputs: {"code": commune_code(inputs["name"])},
    build_defaults=lambda inputs: {
        "name": canonical_name(inputs["name"]),
        # Pas de hiérarchie pour l'instant : converge vers NULL (cf. décision
        # « seules les communes du GeoJSON », raccrochage BDR plus tard).
        "district": None,
        "ville": None,
        "region": None,
        "departement": None,
        "sous_prefecture": None,
    },
    accept=_commune_accept,
))
