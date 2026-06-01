"""
Helpers d'import des frontières HDX. Logique pure, sans I/O Django, pour
faciliter les tests unitaires.

Particularité Côte d'Ivoire :
  HDX (cod-ab-civ) regroupe au niveau ADM1 les 31 régions + 2 districts
  autonomes (33 entités au même niveau). Les 12 districts non-autonomes
  (Lagunes, Vallée du Bandama, …) n'ont pas de géométrie HDX — on les
  reconstruit par ST_Union des régions qu'ils contiennent, via le mapping
  DISTRICT_REGIONS ci-dessous (source : Wikipedia + Stanford SearchWorks).
"""
import unicodedata


# ── Détection des districts autonomes ──────────────────────────────────────
# HDX nomme les autonomes "District Autonome D'Abidjan" / "De Yamoussoukro".
# On matche par contains, pas par égalité.
AUTONOMOUS_TOKENS = ("abidjan", "yamoussoukro")


# ── Mapping District (non-autonome) → liste de régions HDX ─────────────────
# Noms écrits avec leurs diacritiques pour la lisibilité. La comparaison passe
# toujours par normalize_name() qui retire accents, apostrophes, tirets, casse.
DISTRICT_REGIONS = {
    "Bas-Sassandra":      ["Gbôklé", "Nawa", "San-Pédro"],
    "Comoé":              ["Sud-Comoé", "Indénié-Djuablin"],
    "Denguélé":           ["Folon", "Kabadougou"],
    "Gôh-Djiboua":        ["Gôh", "Lôh-Djiboua"],
    "Lacs":               ["Bélier", "Iffou", "Moronou", "N'Zi"],
    "Lagunes":            ["Agnéby-Tiassa", "Grands-Ponts", "Mé"],
    "Montagnes":          ["Cavally", "Guémon", "Tonkpi"],
    "Sassandra-Marahoué": ["Haut-Sassandra", "Marahoué"],
    "Savanes":            ["Bagoué", "Poro", "Tchologo"],
    "Vallée du Bandama":  ["Gbêkê", "Hambol"],
    "Woroba":             ["Bafing", "Béré", "Worodougou"],
    "Zanzan":             ["Bounkani", "Gontougo"],
}


# ── Candidats de noms de colonnes (insensibles à la casse) ─────────────────
ADM1_NAME_CANDIDATES   = ("adm1_name", "ADM1_FR", "ADM1_NAME", "NAME_1", "ADM1_NM", "admin1Name", "name")
ADM1_PCODE_CANDIDATES  = ("adm1_pcode", "ADM1_PCODE", "ADM1_CODE", "admin1Pcod", "PCODE")

ADM2_NAME_CANDIDATES   = ("adm2_name", "ADM2_FR", "ADM2_NAME", "NAME_2", "admin2Name", "name")
ADM2_PCODE_CANDIDATES  = ("adm2_pcode", "ADM2_PCODE", "ADM2_CODE", "admin2Pcod", "PCODE")
ADM2_PARENT_CANDIDATES = ("adm1_pcode", "ADM1_PCODE", "ADM1_CODE", "admin1Pcod", "PARENT_CODE")

ADM3_NAME_CANDIDATES   = ("adm3_name", "ADM3_FR", "ADM3_NAME", "NAME_3", "admin3Name", "name")
ADM3_PCODE_CANDIDATES  = ("adm3_pcode", "ADM3_PCODE", "ADM3_CODE", "admin3Pcod", "PCODE")
ADM3_PARENT_CANDIDATES = ("adm2_pcode", "ADM2_PCODE", "ADM2_CODE", "admin2Pcod", "PARENT_CODE")


# ── Helpers de comparaison de noms ─────────────────────────────────────────

def normalize_name(name):
    """
    Normalise un nom pour comparaison (matching HDX ↔ mapping interne) :
      - retire les accents (NFKD + filtrage combining)
      - lowercase
      - apostrophes supprimées
      - tirets et espaces multiples → espace simple

    Exemples :
      "Agnéby-Tiassa" → "agneby tiassa"
      "Agneby-Tiassa" → "agneby tiassa"   (HDX, sans accent)
      "N'Zi"          → "nzi"
      "Grands-Ponts"  → "grands ponts"
      "Grands Ponts"  → "grands ponts"    (HDX, sans tiret)
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = ascii_name.lower().strip().replace("'", "").replace("-", " ")
    # collapse multiple spaces
    return " ".join(cleaned.split())


def is_autonomous_name(name):
    """
    True si le nom correspond à un district autonome (Abidjan, Yamoussoukro).

    Matche en mode "contains" sur la version normalisée — accepte donc :
      - "Abidjan"
      - "District Autonome D'Abidjan"  ← HDX format
      - "Yamoussoukro"
      - "District Autonome De Yamoussoukro"
    """
    if not name:
        return False
    normalized = normalize_name(name)
    return any(token in normalized for token in AUTONOMOUS_TOKENS)


def clean_autonomous_name(hdx_name):
    """
    Extrait le nom court d'un district autonome depuis le libellé HDX.
    "District Autonome D'Abidjan" → "Abidjan"
    "District Autonome De Yamoussoukro" → "Yamoussoukro"
    """
    normalized = normalize_name(hdx_name)
    if "abidjan" in normalized:
        return "Abidjan"
    if "yamoussoukro" in normalized:
        return "Yamoussoukro"
    return hdx_name


def region_to_district_map():
    """
    Renvoie le mapping inverse {nom_région_normalisé: nom_district}.
    Utilisé pour retrouver le district parent d'une région à l'import.
    """
    mapping = {}
    for district_name, regions in DISTRICT_REGIONS.items():
        for region in regions:
            mapping[normalize_name(region)] = district_name
    return mapping


def district_code(district_name):
    """
    Génère un code stable pour un district non-autonome à partir de son nom.
    On utilise un préfixe CIV-DIS pour ne pas entrer en collision avec les
    codes HDX (CI01-CI33).

    "Lagunes" → "CIV-DIS-LAGUNES"
    "Bas-Sassandra" → "CIV-DIS-BAS-SASSANDRA"
    """
    slug = normalize_name(district_name).upper().replace(" ", "-")
    return f"CIV-DIS-{slug}"


# ── Helpers de matching de colonnes (anciens, conservés) ───────────────────

def first_match(fields, candidates):
    """
    Renvoie le premier nom de champ trouvé dans `fields` correspondant
    (insensible à la casse) à l'un des `candidates`. None si rien.
    """
    fields_lower = {f.lower(): f for f in fields}
    for cand in candidates:
        if cand.lower() in fields_lower:
            return fields_lower[cand.lower()]
    return None


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
