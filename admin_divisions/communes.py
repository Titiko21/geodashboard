"""
Logique de domaine des communes — pure, sans I/O Django.

Sert à la fois à la commande `import_communes` et à la cible « commune »
de l'importeur générique (app `importer`).
"""
from pathlib import Path

from admin_divisions._geo_utils import normalize_name

# Fichier livré avec le dépôt (données reproductibles).
DEFAULT_FILE = Path(__file__).resolve().parent / "data" / "communes_grand_abidjan.geojson"


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
