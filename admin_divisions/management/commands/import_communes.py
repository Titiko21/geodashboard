"""
import_communes — Importe les communes (frontières polygonales) depuis un
fichier GeoJSON.

Cas d'usage initial : fichier « Grand Abidjan » fourni par l'utilisateur, qui
contient 14 communes :
  - les 13 communes du District Autonome d'Abidjan (Abobo, Adjamé, Attécoubé,
    Cocody, Koumassi, Marcory, Plateau, Port-Bouët, Treichville, Yopougon,
    Bingerville, Anyama, Songon) ;
  - Grand-Bassam (région Sud-Comoé → District de la Comoé).

Rattachement au district :
  Le champ Commune.district est obligatoire (FK PROTECT). On résout le district
  parent par NOM (normalisé) parmi les districts existants — pour réutiliser
  celui qu'aurait créé `import_admin_hdx` (source de référence). S'il manque, on
  le crée a minima (sans géométrie) avec un avertissement, afin de ne pas
  bloquer l'import des communes. Les niveaux Région / Département / Sous-préf.
  restent NULL (cohérent avec le district autonome ; calculables par ST_Within
  plus tard).

Idempotent : `update_or_create` par `code` (CIV-COM-<SLUG>). Rejouer la commande
ne crée pas de doublon et met simplement à jour nom / district / géométrie.

Usage :
    python manage.py import_communes
    python manage.py import_communes --file /chemin/communes.geojson
    python manage.py import_communes --dry-run
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from admin_divisions._hdx_utils import district_code, normalize_name, to_multipolygon
from admin_divisions.models import Commune, District

# Fichier livré avec le dépôt (données reproductibles).
DEFAULT_FILE = Path(__file__).resolve().parents[2] / "data" / "communes_grand_abidjan.geojson"

# Champs candidats pour le nom de commune dans les propriétés GeoJSON.
NAME_FIELDS = ("NOMS", "NOM", "name", "NAME")


# ── Noms canoniques (casse + accents corrects) ─────────────────────────────
# Le fichier source écrit p. ex. « Port-bouet » ; on rétablit la forme propre.
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

# ── Rattachement commune → district ────────────────────────────────────────
# Les 13 communes du District Autonome d'Abidjan (clés normalisées).
ABIDJAN_COMMUNES = {
    normalize_name(n) for n in (
        "Abobo", "Adjamé", "Attécoubé", "Cocody", "Koumassi", "Marcory",
        "Plateau", "Port-Bouët", "Treichville", "Yopougon", "Bingerville",
        "Anyama", "Songon",
    )
}

# Communes hors district autonome : nom normalisé → (district, is_autonomous).
DISTRICT_OVERRIDES = {
    "grand bassam": ("Comoé", False),
}


# ── Helpers purs (testables sans base) ─────────────────────────────────────

def commune_code(name):
    """Code stable et unique d'une commune. « Cocody » → « CIV-COM-COCODY »."""
    slug = normalize_name(name).upper().replace(" ", "-")
    return f"CIV-COM-{slug}"


def canonical_name(name):
    """Nom d'affichage propre, sinon le nom source tel quel."""
    return CANONICAL_NAMES.get(normalize_name(name), name)


def district_for_commune(name):
    """
    Renvoie (nom_district, is_autonomous) pour une commune, ou None si la
    commune n'est pas reconnue (le fichier Grand Abidjan est un ensemble fermé).
    """
    norm = normalize_name(name)
    if norm in DISTRICT_OVERRIDES:
        return DISTRICT_OVERRIDES[norm]
    if norm in ABIDJAN_COMMUNES:
        return ("Abidjan", True)
    return None


def _feature_name(props):
    for field in NAME_FIELDS:
        if props.get(field):
            return props[field]
    return None


class Command(BaseCommand):
    help = (
        "Importe les communes (polygones) depuis un GeoJSON et les rattache à "
        "leur district. Idempotent (update_or_create par code)."
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

        # Cache des districts existants, indexé par nom normalisé.
        district_cache = {normalize_name(d.name): d for d in District.objects.all()}
        created_districts = []

        def resolve_district(name, is_autonomous):
            """Récupère le district par nom, le crée a minima si absent."""
            norm = normalize_name(name)
            existing = district_cache.get(norm)
            if existing is not None:
                return existing
            district = District(
                code=district_code(name),
                name=name,
                is_autonomous=is_autonomous,
            )
            if not dry_run:
                district.save()
            district_cache[norm] = district
            created_districts.append(name)
            return district

        created, updated, skipped = 0, 0, 0

        with transaction.atomic():
            for feat in features:
                props = feat.get("properties", {}) or {}
                raw_name = _feature_name(props)
                if not raw_name:
                    self.stderr.write("  Feature sans nom — ignorée")
                    skipped += 1
                    continue

                mapping = district_for_commune(raw_name)
                if mapping is None:
                    self.stderr.write(
                        f"  Commune non reconnue : « {raw_name} » — ignorée "
                        "(district parent inconnu)"
                    )
                    skipped += 1
                    continue

                district_name, is_autonomous = mapping

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

                district = resolve_district(district_name, is_autonomous)
                code = commune_code(raw_name)
                display = canonical_name(raw_name)

                if dry_run:
                    self.stdout.write(
                        f"  {display:14s} → {code:22s} | district {district_name}"
                    )
                    continue

                _, was_created = Commune.objects.update_or_create(
                    code=code,
                    defaults={
                        "name": display,
                        "district": district,
                        "geom": multipoly,
                    },
                )
                if was_created:
                    created += 1
                    self.stdout.write(f"  + {display} ({code})")
                else:
                    updated += 1
                    self.stdout.write(f"  ~ {display} ({code}) — mis à jour")

            if dry_run:
                transaction.set_rollback(True)

        # ── Bilan ───────────────────────────────────────────────────────────
        if created_districts:
            self.stdout.write(self.style.WARNING(
                "\n  Districts créés a minima (à réconcilier via import_admin_hdx) : "
                + ", ".join(created_districts)
            ))

        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 50}"))
        self.stdout.write(self.style.SUCCESS(
            "Import communes terminé" + (" (DRY RUN)" if dry_run else "")
        ))
        self.stdout.write(f"  Créées      : {created}")
        self.stdout.write(f"  Mises à jour: {updated}")
        if skipped:
            self.stdout.write(f"  Ignorées    : {skipped}")
        self.stdout.write(f"  Total base  : {Commune.objects.count()} communes")
