"""
Helpers géométriques et de nommage, logique pure (sans I/O Django), pour
l'import des découpages administratifs et leurs tests.

Extraits de l'ancien `_hdx_utils` (importeur HDX supprimé) : seules les
fonctions réutilisées par `import_communes` et le futur importeur générique
(Phase A) sont conservées.
"""
import unicodedata


def normalize_name(name):
    """
    Normalise un nom pour comparaison :
      - retire les accents (NFKD + filtrage combining)
      - lowercase
      - apostrophes supprimées
      - tirets et espaces multiples → espace simple

    Exemples :
      "Agnéby-Tiassa" → "agneby tiassa"
      "N'Zi"          → "nzi"
      "Grands-Ponts"  → "grands ponts"
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = ascii_name.lower().strip().replace("'", "").replace("-", " ")
    return " ".join(cleaned.split())


def district_code(district_name):
    """
    Génère un code stable pour un district à partir de son nom.
    Préfixe CIV-DIS pour éviter les collisions.

    "Lagunes" → "CIV-DIS-LAGUNES"
    "Bas-Sassandra" → "CIV-DIS-BAS-SASSANDRA"
    """
    slug = normalize_name(district_name).upper().replace(" ", "-")
    return f"CIV-DIS-{slug}"


def to_multipolygon(geom):
    """
    Normalise une géométrie en MultiPolygon (requis par le champ Django).

    - MultiPolygon → renvoyé tel quel
    - Polygon → enveloppé en MultiPolygon
    - autre → None (caller décide quoi faire)
    """
    from django.contrib.gis.geos import MultiPolygon

    if geom is None:
        return None
    if geom.geom_type == "MultiPolygon":
        return geom
    if geom.geom_type == "Polygon":
        return MultiPolygon(geom, srid=geom.srid)
    return None
