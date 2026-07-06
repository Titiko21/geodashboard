"""
import_communes — Importe les communes (frontières polygonales) depuis un
fichier GeoJSON, crée leurs villes de rattachement et fait le lien.

Cas d'usage initial : fichier « Grand Abidjan » fourni par l'utilisateur, qui
contient 14 communes :
  - les 13 communes du District Autonome d'Abidjan (Abobo, Adjamé, Attécoubé,
    Cocody, Koumassi, Marcory, Plateau, Port-Bouët, Treichville, Yopougon,
    Bingerville, Anyama, Songon) → regroupées dans la **Ville d'Abidjan** ;
  - Grand-Bassam (région Sud-Comoé → District de la Comoé) → **Ville de
    Grand-Bassam** (1 commune).

Modèle (hiérarchie figée) : District → … → Ville → Commune.
  - Une Ville regroupe 1..N communes (FK `Commune.ville`).
  - La géométrie d'une Ville = union des polygones de ses communes.

Rattachement au district :
  Commune.district / Ville.district sont obligatoires (FK PROTECT). On résout le
  district parent par NOM (normalisé) parmi les districts existants. S'il
  manque, on le crée a minima (sans géométrie) avec un avertissement.

Idempotent : `update_or_create` par `code` (CIV-VIL-<SLUG> / CIV-COM-<SLUG>).
Rejouer la commande ne crée pas de doublon.

Usage :
    python manage.py import_communes
    python manage.py import_communes --file /chemin/communes.geojson
    python manage.py import_communes --dry-run
"""
import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from admin_divisions._geo_utils import district_code, normalize_name, to_multipolygon
from admin_divisions.models import Commune, District, Ville

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


def ville_code(name):
    """Code stable et unique d'une ville. « Abidjan » → « CIV-VIL-ABIDJAN »."""
    slug = normalize_name(name).upper().replace(" ", "-")
    return f"CIV-VIL-{slug}"


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


def ville_for_commune(name):
    """
    Nom de la ville de rattachement d'une commune (None si inconnue).
    Les 13 communes d'Abidjan → Ville d'Abidjan ; les autres sont leur propre
    ville (cas Grand-Bassam : Ville de Grand-Bassam = 1 commune).
    """
    norm = normalize_name(name)
    if norm in ABIDJAN_COMMUNES:
        return "Abidjan"
    if norm in DISTRICT_OVERRIDES:
        return canonical_name(name)
    return None


def _feature_name(props):
    for field in NAME_FIELDS:
        if props.get(field):
            return props[field]
    return None


class Command(BaseCommand):
    help = (
        "Importe les communes (polygones) depuis un GeoJSON, crée leurs villes "
        "de rattachement et fait le lien. Idempotent (update_or_create par code)."
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

            mapping = district_for_commune(raw_name)
            ville_name = ville_for_commune(raw_name)
            if mapping is None or ville_name is None:
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

            district_name, is_autonomous = mapping
            records.append({
                "display":        canonical_name(raw_name),
                "code":           commune_code(raw_name),
                "district_name":  district_name,
                "is_autonomous":  is_autonomous,
                "ville_name":     ville_name,
                "multipoly":      multipoly,
            })

        # ── 2. Groupement par ville ─────────────────────────────────────────
        groups = defaultdict(list)
        for rec in records:
            groups[rec["ville_name"]].append(rec)

        created_v, updated_v, created_c, updated_c = 0, 0, 0, 0
        ville_objs = {}

        with transaction.atomic():
            # ── 3. Villes (géométrie = union des communes membres) ──────────
            for ville_name, members in sorted(groups.items()):
                district = resolve_district(
                    members[0]["district_name"], members[0]["is_autonomous"]
                )
                geoms = [m["multipoly"] for m in members if m["multipoly"] is not None]
                union = None
                if geoms:
                    acc = geoms[0]
                    for g in geoms[1:]:
                        acc = acc.union(g)
                    union = to_multipolygon(acc) or acc

                vcode = ville_code(ville_name)
                if dry_run:
                    self.stdout.write(
                        f"  [Ville] {ville_name} ({vcode}) ← {len(members)} commune(s) "
                        f"| district {members[0]['district_name']}"
                    )
                    ville_objs[ville_name] = None
                    continue

                ville, v_created = Ville.objects.update_or_create(
                    code=vcode,
                    defaults={"name": ville_name, "district": district, "geom": union},
                )
                ville_objs[ville_name] = ville
                if v_created:
                    created_v += 1
                    self.stdout.write(f"  + Ville {ville_name} ({vcode}) ← {len(members)} commune(s)")
                else:
                    updated_v += 1
                    self.stdout.write(f"  ~ Ville {ville_name} ({vcode}) — mise à jour")

            # ── 4. Communes (rattachées à leur ville + district) ────────────
            for rec in sorted(records, key=lambda r: r["display"]):
                if dry_run:
                    self.stdout.write(
                        f"  {rec['display']:14s} → {rec['code']:22s} "
                        f"| ville {rec['ville_name']} | district {rec['district_name']}"
                    )
                    continue

                district = resolve_district(rec["district_name"], rec["is_autonomous"])
                _, c_created = Commune.objects.update_or_create(
                    code=rec["code"],
                    defaults={
                        "name":     rec["display"],
                        "district": district,
                        "ville":    ville_objs[rec["ville_name"]],
                        "geom":     rec["multipoly"],
                    },
                )
                if c_created:
                    created_c += 1
                    self.stdout.write(f"    + {rec['display']} ({rec['code']})")
                else:
                    updated_c += 1
                    self.stdout.write(f"    ~ {rec['display']} ({rec['code']}) — mise à jour")

            if dry_run:
                transaction.set_rollback(True)

        # ── Bilan ───────────────────────────────────────────────────────────
        if created_districts:
            self.stdout.write(self.style.WARNING(
                "\n  Districts créés a minima (géométrie à compléter ultérieurement) : "
                + ", ".join(created_districts)
            ))

        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 50}"))
        self.stdout.write(self.style.SUCCESS(
            "Import villes + communes terminé" + (" (DRY RUN)" if dry_run else "")
        ))
        self.stdout.write(f"  Villes      : {created_v} créées, {updated_v} mises à jour")
        self.stdout.write(f"  Communes    : {created_c} créées, {updated_c} mises à jour")
        if skipped:
            self.stdout.write(f"  Ignorées    : {skipped}")
        self.stdout.write(
            f"  Total base  : {Ville.objects.count()} villes, "
            f"{Commune.objects.count()} communes"
        )
