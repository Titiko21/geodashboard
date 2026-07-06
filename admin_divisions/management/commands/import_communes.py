"""
import_communes — Importe les communes (frontières polygonales) depuis un
fichier GeoJSON. C'est la SEULE source de données actuelle de l'application.

Cas d'usage : fichier « Grand Abidjan » fourni par l'utilisateur, qui
contient 14 communes (les 13 du Grand Abidjan + Grand-Bassam).

Les communes sont importées SEULES : aucun district ni ville n'est créé —
les FK de rattachement (district, ville, région…) restent NULL. La
hiérarchie sera raccrochée plus tard lors des imports BDR.

Idempotent : `update_or_create` par `code` (CIV-COM-<SLUG>). Rejouer la
commande ne crée pas de doublon et reconverge vers l'état cible
(FK hiérarchiques remises à NULL).

Usage :
    python manage.py import_communes
    python manage.py import_communes --file /chemin/communes.geojson
    python manage.py import_communes --dry-run
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from admin_divisions._geo_utils import normalize_name, to_multipolygon
from admin_divisions.models import Commune

# Fichier livré avec le dépôt (données reproductibles).
DEFAULT_FILE = Path(__file__).resolve().parents[2] / "data" / "communes_grand_abidjan.geojson"

# Champs candidats pour le nom de commune dans les propriétés GeoJSON.
NAME_FIELDS = ("NOMS", "NOM", "name", "NAME")


# ── Noms canoniques (casse + accents corrects) ─────────────────────────────
# Le fichier source écrit p. ex. « Port-bouet » ; on rétablit la forme propre.
# Sert aussi d'ensemble fermé des communes reconnues.
CANONICAL_NAMES = {
    "abobo": "Abobo",
    "adjame": "Adjamé",
    "attecoube": "Attécoubé",
    "cocody": "Cocody",
    "koumassi": "Koumassi",
    "marcory": "Marcory",
    "plateau": "Plateau",
    "port bouet": "Port-Bouët",
    "treichville": "Treichville",
    "yopougon": "Yopougon",
    "bingerville": "Bingerville",
    "anyama": "Anyama",
    "songon": "Songon",
    "grand bassam": "Grand-Bassam",
}


# ── Helpers purs (testables sans base) ─────────────────────────────────────

def commune_code(name):
    """Code stable et unique d'une commune. « Cocody » → « CIV-COM-COCODY »."""
    slug = normalize_name(name).upper().replace(" ", "-")
    return f"CIV-COM-{slug}"


def canonical_name(name):
    """Nom d'affichage propre, sinon le nom source tel quel."""
    return CANONICAL_NAMES.get(normalize_name(name), name)


def is_known_commune(name):
    """True si la commune fait partie de l'ensemble fermé du fichier livré."""
    return normalize_name(name) in CANONICAL_NAMES


def _feature_name(props):
    for field in NAME_FIELDS:
        if props.get(field):
            return props[field]
    return None


class Command(BaseCommand):
    help = (
        "Importe les communes (polygones) depuis un GeoJSON — seule source de "
        "données actuelle. Idempotent (update_or_create par code)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=str(DEFAULT_FILE),
            help=f"Chemin du GeoJSON (défaut : {DEFAULT_FILE}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simule sans écrire en base.",
        )

    def handle(self, *args, **options):
        from django.contrib.gis.geos import GEOSGeometry

        path = Path(options["file"])
        dry_run = options["dry_run"]

        if not path.exists():
            raise CommandError(f"Fichier introuvable : {path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise CommandError(f"Lecture GeoJSON impossible : {exc}")

        features = data.get("features", [])
        if not features:
            raise CommandError("Aucune feature dans le GeoJSON.")

        if dry_run:
            self.stdout.write(self.style.WARNING("── DRY RUN ── aucune écriture\n"))

        # ── 1. Lecture des features → enregistrements normalisés ────────────
        records = []
        skipped = 0
        for feat in features:
            props = feat.get("properties", {}) or {}
            raw_name = _feature_name(props)
            if not raw_name:
                self.stderr.write("  Feature sans nom — ignorée")
                skipped += 1
                continue

            if not is_known_commune(raw_name):
                self.stderr.write(
                    f"  Commune non reconnue : « {raw_name} » — ignorée"
                )
                skipped += 1
                continue

            geom_dict = feat.get("geometry")
            if not geom_dict:
                self.stderr.write(f"  {raw_name} : géométrie absente — ignorée")
                skipped += 1
                continue
            try:
                geom = GEOSGeometry(json.dumps(geom_dict), srid=4326)
                multipoly = to_multipolygon(geom)
            except Exception as exc:
                self.stderr.write(f"  {raw_name} : géométrie invalide ({exc}) — ignorée")
                skipped += 1
                continue
            if multipoly is None:
                self.stderr.write(
                    f"  {raw_name} : géométrie {geom.geom_type} non polygonale — ignorée"
                )
                skipped += 1
                continue

            records.append({
                "display":   canonical_name(raw_name),
                "code":      commune_code(raw_name),
                "multipoly": multipoly,
            })

        # ── 2. Communes (sans rattachement hiérarchique) ────────────────────
        created_c, updated_c = 0, 0
        with transaction.atomic():
            for rec in sorted(records, key=lambda r: r["display"]):
                if dry_run:
                    self.stdout.write(
                        f"  {rec['display']:14s} → {rec['code']}"
                    )
                    continue

                _, c_created = Commune.objects.update_or_create(
                    code=rec["code"],
                    defaults={
                        "name":            rec["display"],
                        "geom":            rec["multipoly"],
                        # Pas de hiérarchie pour l'instant : converge vers NULL.
                        "district":        None,
                        "ville":           None,
                        "region":          None,
                        "departement":     None,
                        "sous_prefecture": None,
                    },
                )
                if c_created:
                    created_c += 1
                    self.stdout.write(f"  + {rec['display']} ({rec['code']})")
                else:
                    updated_c += 1
                    self.stdout.write(f"  ~ {rec['display']} ({rec['code']}) — mise à jour")

            if dry_run:
                transaction.set_rollback(True)

        # ── Bilan ───────────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 50}"))
        self.stdout.write(self.style.SUCCESS(
            "Import communes terminé" + (" (DRY RUN)" if dry_run else "")
        ))
        self.stdout.write(f"  Communes    : {created_c} créées, {updated_c} mises à jour")
        if skipped:
            self.stdout.write(f"  Ignorées    : {skipped}")
        self.stdout.write(f"  Total base  : {Commune.objects.count()} communes")
