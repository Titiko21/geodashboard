"""
GéoDash — populate_geodata.py
Commande Django pour importer des données géospatiales depuis OpenStreetMap (Overpass API).

Optimisation clé : une seule requête Overpass par zone (routes + eau + végétation),
ce qui évite le throttling et les timeouts sur la troisième requête.

Usage :
    python manage.py populate_geodata                  # crée les zones CI + importe tout
    python manage.py populate_geodata --zone MAN       # une seule zone
    python manage.py populate_geodata --dry-run        # simulation sans écriture
    python manage.py populate_geodata --clear          # repart de zéro
    python manage.py populate_geodata --roads-only     # routes uniquement
"""

import logging
import math
import os
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from dashboard.models import Alert, FloodRisk, RoadSegment, VegetationDensity, Zone

logger = logging.getLogger(__name__)


# ─── Configuration ────────────────────────────────────────────────────────────

OVERPASS_URL     = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_TIMEOUT = 90     # secondes — plus long car requête fusionnée = plus de données
REQUEST_DELAY    = 4.0    # secondes entre zones (respecter fair-use Overpass)
MAX_RETRIES      = 3      # tentatives avant abandon
RETRY_DELAY      = 15.0   # secondes entre deux tentatives
SEARCH_RADIUS    = 0.05   # degrés ≈ 5 km autour du centre
MAX_ELEMENTS     = 500    # limite par type d'élément dans la requête fusionnée


# ─── Villes de Côte d'Ivoire ──────────────────────────────────────────────────

COTE_IVOIRE_VILLES = [
    # (nom, code, lat, lng, description)
    ("Abidjan",        "ABJ",  5.3600,  -4.0083, "Capitale économique, plus grande ville du pays"),
    ("Abobo",          "ABO",  5.4167,  -4.0167, "Commune nord d'Abidjan"),
    ("Adjamé",         "ADJ",  5.3667,  -4.0167, "Commune centrale d'Abidjan"),
    ("Cocody",         "COC",  5.3667,  -3.9667, "Commune résidentielle d'Abidjan"),
    ("Yopougon",       "YOP",  5.3500,  -4.0833, "Plus grande commune d'Abidjan"),
    ("Marcory",        "MAR",  5.3000,  -3.9833, "Commune sud d'Abidjan"),
    ("Koumassi",       "KOU",  5.2833,  -3.9667, "Commune industrielle d'Abidjan"),
    ("Port-Bouët",     "PBO",  5.2500,  -3.9333, "Commune aéroportuaire d'Abidjan"),
    ("Treichville",    "TRE",  5.2833,  -4.0000, "Commune portuaire d'Abidjan"),
    ("Plateau",        "PLT",  5.3167,  -4.0167, "Centre des affaires d'Abidjan"),
    ("Attécoubé",      "ATT",  5.3500,  -4.0500, "Commune ouest d'Abidjan"),
    ("Bingerville",    "BNG",  5.3500,  -3.8833, "Ancienne capitale coloniale"),
    ("Yamoussoukro",   "YAM",  6.8276,  -5.2893, "Capitale politique, basilique Notre-Dame de la Paix"),
    ("Bouaké",         "BOU",  7.6833,  -5.0333, "Deuxième ville, centre commercial du pays"),
    ("Daloa",          "DAL",  6.8833,  -6.4500, "Troisième ville, région du Haut-Sassandra"),
    ("San-Pédro",      "SAN",  4.7500,  -6.6333, "Port économique du sud-ouest"),
    ("Korhogo",        "KOR",  9.4500,  -5.6333, "Capitale du nord, culture senoufo"),
    ("Man",            "MAN",  7.4125,  -7.5539, "Ville des montagnes, région du Tonkpi"),
    ("Abengourou",     "ABE",  6.7333,  -3.4833, "Capitale de l'Indénié-Djuablin"),
    ("Divo",           "DIV",  5.8333,  -5.3667, "Région du Lôh-Djiboua"),
    ("Gagnoa",         "GAG",  6.1333,  -5.9500, "Capitale du Gôh"),
    ("Soubré",         "SOU",  5.7833,  -6.6000, "Capitale de la Nawa, zone cacaoyère"),
    ("Agboville",      "AGB",  5.9333,  -4.2167, "Région de l'Agnéby-Tiassa"),
    ("Grand-Bassam",   "GBA",  5.2000,  -3.7333, "Patrimoine UNESCO, ancienne capitale"),
    ("Sassandra",      "SAS",  4.9500,  -6.0833, "Port de pêche historique"),
    ("Dimbokro",       "DIM",  6.6500,  -4.7000, "Région de l'Iffou"),
    ("Bondoukou",      "BDK",  8.0333,  -2.8000, "Région du Gontougo, mosquée historique"),
    ("Séguéla",        "SEG",  7.9667,  -6.6667, "Capitale du Worodougou"),
    ("Odienné",        "ODI",  9.5000,  -7.5667, "Capitale du Kabadougou"),
    ("Touba",          "TBA",  8.2833,  -7.6833, "Région du Bafing"),
    ("Mankono",        "MNK",  8.0583,  -6.1833, "Région du Béré"),
    ("Katiola",        "KAT",  8.1333,  -5.1000, "Région du Hambol"),
    ("Ferkessédougou", "FER",  9.5833,  -5.2000, "Carrefour nord, industrie sucrière"),
    ("Bouna",          "BNA",  9.2667,  -3.0000, "Région du Bounkani, parc de la Comoé"),
    ("Boundiali",      "BDI",  9.5167,  -6.4833, "Région du Poro"),
    ("Tingréla",       "TIN", 10.4833,  -6.1333, "Frontière nord avec le Mali"),
    ("Toumodi",        "TMD",  6.5500,  -5.0167, "Région du Bélier"),
    ("Tiassalé",       "TIA",  5.8833,  -4.8167, "Région de l'Agnéby-Tiassa"),
    ("Adzopé",         "ADZ",  6.1000,  -3.8667, "Région de la Mé"),
    ("Anyama",         "ANY",  5.5000,  -4.0500, "Banlieue nord d'Abidjan"),
    ("Dabou",          "DAB",  5.3167,  -4.3833, "Région des Grands-Ponts"),
    ("Grand-Lahou",    "GLA",  5.1333,  -5.0167, "Lagune Tagba"),
    ("Lakota",         "LAK",  5.8500,  -5.6833, "Région du Lôh-Djiboua"),
    ("Issia",          "ISS",  6.4833,  -6.5833, "Région du Haut-Sassandra"),
    ("Vavoua",         "VAV",  7.3833,  -6.4667, "Région du Haut-Sassandra"),
    ("Guiglo",         "GUI",  6.5333,  -7.4833, "Région du Cavally"),
    ("Bloléquin",      "BLQ",  6.4667,  -8.0000, "Région du Guémon"),
    ("Toulepleu",      "TLP",  6.5833,  -8.4000, "Frontière ouest avec le Liberia"),
    ("Danané",         "DAN",  7.2667,  -8.1500, "Région du Tonkpi, frontière Guinée"),
    ("Biankouma",      "BIA",  7.7333,  -7.6167, "Région du Tonkpi"),
    ("Bangolo",        "BAG",  7.0167,  -7.4833, "Région du Guémon"),
    ("Duekoué",        "DUE",  6.7333,  -7.3500, "Région du Guémon"),
    ("Tabou",          "TAB",  4.4167,  -7.3500, "Frontière sud-ouest"),
    ("Grand-Béréby",   "GBR",  4.6333,  -6.9000, "Côte balnéaire sud-ouest"),
    ("Fresco",         "FRE",  5.0500,  -5.5667, "Côte balnéaire, pêche"),
    ("Daoukro",        "DAO",  7.0667,  -3.9667, "Région de l'Iffou"),
    ("Tanda",          "TDA",  7.8000,  -3.1667, "Région du Gontougo"),
    ("Agnibilékrou",   "AGN",  7.1333,  -3.2000, "Frontière est avec le Ghana"),
    ("Aboisso",        "ABS",  5.4667,  -3.2000, "Région du Sud-Comoé"),
    ("Adiaké",         "ADA",  5.2833,  -3.3000, "Lagune Tendo, frontière Ghana"),
    ("Bongouanou",     "BOG",  6.6500,  -4.2000, "Région de l'Iffou"),
    ("M'Bahiakro",     "MBH",  7.4500,  -4.3333, "Région de l'Iffou"),
    ("Bocanda",        "BOC",  7.0667,  -4.5167, "Région du N'Zi"),
    ("Oumé",           "OUM",  6.3833,  -5.4167, "Région du Gôh"),
    ("Tiébissou",      "TIB",  7.1500,  -5.2333, "Région du Bélier"),
    ("Didiévi",        "DDV",  6.8833,  -5.3167, "Région du Bélier"),
    ("Akoupé",         "AKP",  6.3833,  -3.8667, "Région de la Mé"),
    ("Jacqueville",    "JAC",  5.2000,  -4.4167, "Péninsule des Grands-Ponts"),
    ("Zouan-Hounien",  "ZOH",  6.9167,  -8.3333, "Région du Tonkpi"),
    ("Koun-Fao",       "KFO",  7.3500,  -3.0167, "Région du Gontougo"),
]


# ─── Tables de correspondance OSM → modèle Django ────────────────────────────

OSM_SURFACE_MAP = {
    "asphalt": "bitume", "paved": "bitume", "concrete": "bitume",
    "cobblestone": "pave", "sett": "pave", "paving_stones": "pave",
    "unpaved": "terre", "dirt": "terre", "earth": "terre", "mud": "terre",
    "gravel": "gravier", "fine_gravel": "gravier", "compacted": "gravier",
}

HIGHWAY_SURFACE_FALLBACK = {
    "motorway": "bitume", "trunk": "bitume", "primary": "bitume",
    "secondary": "bitume", "tertiary": "bitume", "residential": "bitume",
    "service": "bitume", "unclassified": "terre", "track": "terre",
    "path": "terre", "footway": "terre",
}

HIGHWAY_BASE_SCORE = {
    "motorway": 88, "trunk": 82, "primary": 75, "secondary": 65,
    "tertiary": 55, "residential": 58, "service": 52,
    "unclassified": 38, "track": 28, "path": 22, "footway": 18,
}

HIGHWAY_LABEL = {
    "motorway": "Autoroute", "trunk": "Route nationale",
    "primary": "Route principale", "secondary": "Route secondaire",
    "tertiary": "Route tertiaire", "residential": "Voie résidentielle",
    "unclassified": "Route non classée", "track": "Piste",
    "path": "Chemin", "footway": "Sentier", "service": "Voie de service",
}

SMOOTHNESS_DELTA = {
    "excellent": +15, "good": +8, "intermediate": 0,
    "bad": -15, "very_bad": -25, "horrible": -35, "impassable": -50,
}

FLOOD_SCORE_RANGE = {
    "river": (60, 85), "canal": (55, 78), "stream": (35, 60),
    "wetland": (42, 72), "water": (25, 55),
}

# Plages NDVI réalistes pour la zone tropicale de Côte d'Ivoire
NDVI_RANGE = {
    "forest": (0.62, 0.88), "wood": (0.60, 0.86),
    "orchard": (0.48, 0.72), "grass": (0.28, 0.55),
    "meadow": (0.30, 0.58), "grassland": (0.28, 0.52),
    "scrub": (0.22, 0.48), "heath": (0.18, 0.42),
    "farmland": (0.15, 0.40),
}

HIGHWAY_TAGS = (
    "motorway|trunk|primary|secondary|tertiary|residential|unclassified|track"
)

WATER_NATURAL_TAGS  = "wetland|water"
WATER_WAY_TAGS      = "river|stream|canal"
LANDUSE_TAGS        = "forest|grass|meadow|orchard|farmland"
NATURAL_VEG_TAGS    = "wood|scrub|grassland|heath"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_bbox(lat: float, lng: float, r: float = SEARCH_RADIUS) -> str:
    return f"{lat - r},{lng - r},{lat + r},{lng + r}"


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def surface_from_tags(highway: str, tags: dict) -> str:
    return (OSM_SURFACE_MAP.get(tags.get("surface", ""))
            or HIGHWAY_SURFACE_FALLBACK.get(highway, "autre"))


def score_from_tags(highway: str, tags: dict) -> int:
    score = HIGHWAY_BASE_SCORE.get(highway, 40)
    score += SMOOTHNESS_DELTA.get(tags.get("smoothness", ""), 0)
    if tags.get("surface") in ("paved", "asphalt", "concrete"):
        score = max(score, 55)
    elif tags.get("surface") in ("unpaved", "dirt", "mud"):
        score = min(score, 45)
    return max(5, min(100, score))


def status_from_score(score: int) -> str:
    if score >= 70: return "bon"
    if score >= 45: return "degrade"
    if score >= 20: return "critique"
    return "ferme"


def ndvi_to_density(ndvi: float) -> str:
    if ndvi < 0.2: return "sparse"
    if ndvi < 0.4: return "moderate"
    if ndvi < 0.6: return "dense"
    return "very_dense"


# ─── Overpass : requête unique fusionnée avec retry ───────────────────────────

def overpass_fetch(zone_bbox: str, roads_only: bool, stdout) -> dict | None:
    """
    Une seule requête Overpass par zone couvrant routes + eau + végétation.
    Avantage : 1 appel réseau au lieu de 3, pas de throttling inter-requêtes.
    Retry automatique jusqu'à MAX_RETRIES fois en cas d'échec.
    """
    if roads_only:
        query = f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:100000000];
        (
          way["highway"~"^({HIGHWAY_TAGS})$"]({zone_bbox});
        );
        out body geom {MAX_ELEMENTS};
        """
    else:
        query = f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:100000000];
        (
          way["highway"~"^({HIGHWAY_TAGS})$"]({zone_bbox});
          way["natural"~"^({WATER_NATURAL_TAGS})$"]({zone_bbox});
          way["waterway"~"^({WATER_WAY_TAGS})$"]({zone_bbox});
          way["landuse"~"^({LANDUSE_TAGS})$"]({zone_bbox});
          way["natural"~"^({NATURAL_VEG_TAGS})$"]({zone_bbox});
        );
        out body geom {MAX_ELEMENTS};
        """

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=OVERPASS_TIMEOUT,
                headers={"User-Agent": "GéoDash/1.0 (contact@geodash-ci.example.com)"},
            )
            resp.raise_for_status()
            data = resp.json()

            if "remark" in data and "error" in data.get("remark", "").lower():
                raise ValueError(f"Overpass remark: {data['remark']}")

            return data

        except requests.exceptions.Timeout:
            msg = f"Timeout Overpass (tentative {attempt}/{MAX_RETRIES})"
            logger.warning(msg)
            stdout.write(f"  ⚠ {msg}")
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            msg  = f"Erreur HTTP {code} (tentative {attempt}/{MAX_RETRIES})"
            logger.warning(msg)
            stdout.write(f"  ⚠ {msg}")
            if code == 429:
                wait = RETRY_DELAY * 3
                stdout.write(f"  → Trop de requêtes (429), attente {wait}s...")
                time.sleep(wait)
                continue
        except requests.exceptions.RequestException as e:
            msg = f"Erreur réseau : {e} (tentative {attempt}/{MAX_RETRIES})"
            logger.warning(msg)
            stdout.write(f"  ⚠ {msg}")
        except ValueError as e:
            logger.error("Réponse Overpass invalide : %s", e)
            stdout.write(f"  ✗ Réponse invalide : {e}")
            return None

        if attempt < MAX_RETRIES:
            stdout.write(f"  → Nouvelle tentative dans {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    logger.error("Overpass inaccessible après %d tentatives", MAX_RETRIES)
    return None


# ─── Routage des éléments OSM ─────────────────────────────────────────────────

def classify_element(el: dict) -> str:
    """
    Détermine la catégorie d'un élément OSM : 'road', 'flood', 'vegetation' ou None.
    Appelé une fois par élément après la requête fusionnée.
    """
    tags = el.get("tags", {})
    if tags.get("highway") in HIGHWAY_TAGS.split("|"):
        return "road"
    if tags.get("waterway") in WATER_WAY_TAGS.split("|"):
        return "flood"
    if tags.get("natural") in WATER_NATURAL_TAGS.split("|"):
        return "flood"
    if tags.get("landuse") in LANDUSE_TAGS.split("|"):
        return "vegetation"
    if tags.get("natural") in NATURAL_VEG_TAGS.split("|"):
        return "vegetation"
    return None


# ─── Création automatique des zones ──────────────────────────────────────────

def create_zones_if_missing(stdout) -> int:
    existing = set(Zone.objects.values_list("code", flat=True))
    to_create = [
        Zone(name=n, code=c, lat_center=lat, lng_center=lng, description=d)
        for n, c, lat, lng, d in COTE_IVOIRE_VILLES
        if c not in existing
    ]
    if to_create:
        Zone.objects.bulk_create(to_create)
        for z in to_create:
            stdout.write(f"  + {z.name} ({z.code})")
    return len(to_create)


# ─── Persistance en base ──────────────────────────────────────────────────────

def save_roads(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    now = timezone.now()
    existing = {
        r.name: r
        for r in RoadSegment.objects.filter(zone=zone)
        .only("id", "name", "condition_score", "status",
              "surface_type", "geojson", "notes", "last_analyzed")
    }
    to_create, to_update = [], []

    for el in elements:
        tags    = el.get("tags", {})
        highway = tags.get("highway", "unclassified")
        name    = (tags.get("name") or tags.get("ref")
                   or f"{HIGHWAY_LABEL.get(highway, highway)} #{el['id']}")
        geometry = el.get("geometry", [])
        if len(geometry) < 2:
            continue

        geojson = {"type": "LineString",
                   "coordinates": [[p["lon"], p["lat"]] for p in geometry]}
        score   = score_from_tags(highway, tags)
        status  = status_from_score(score)
        surface = surface_from_tags(highway, tags)

        parts = [f"Type OSM : {highway}"]
        if tags.get("maxspeed"):   parts.append(f"Vitesse max : {tags['maxspeed']} km/h")
        if tags.get("lanes"):      parts.append(f"Voies : {tags['lanes']}")
        if tags.get("smoothness"): parts.append(f"État OSM : {tags['smoothness']}")
        notes = " | ".join(parts)

        if name in existing:
            r = existing[name]
            r.condition_score = score
            r.status          = status
            r.surface_type    = surface
            r.geojson         = geojson
            r.notes           = notes
            r.last_analyzed   = now
            to_update.append(r)
        else:
            to_create.append(RoadSegment(
                zone=zone, name=name, status=status,
                condition_score=score, surface_type=surface,
                geojson=geojson, notes=notes, last_analyzed=now,
            ))

    with transaction.atomic():
        if to_create:
            RoadSegment.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            RoadSegment.objects.bulk_update(
                to_update,
                ["condition_score", "status", "surface_type",
                 "geojson", "notes", "last_analyzed"],
                batch_size=200,
            )

    return len(to_create), len(to_update)


def save_flood_risks(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    now = timezone.now()
    existing = {
        f.name: f
        for f in FloodRisk.objects.filter(zone=zone)
        .only("id", "name", "risk_level", "risk_score",
              "area_km2", "geojson", "last_analyzed")
    }
    to_create, to_update = [], []

    for el in elements:
        tags     = el.get("tags", {})
        waterway = tags.get("waterway", "")
        natural  = tags.get("natural", "")
        name     = tags.get("name") or tags.get("ref") or f"Zone hydro #{el['id']}"
        key      = waterway or natural
        lo, hi   = FLOOD_SCORE_RANGE.get(key, (20, 50))
        score    = round(lo + (el["id"] % 1000) / 1000.0 * (hi - lo), 1)

        if score >= 70:   risk_level = "critique"
        elif score >= 50: risk_level = "eleve"
        elif score >= 30: risk_level = "modere"
        else:             risk_level = "faible"

        geometry = el.get("geometry", [])
        if geometry:
            lats = [p["lat"] for p in geometry]
            lngs = [p["lon"] for p in geometry]
            area_km2 = round(
                haversine_km(min(lats), min(lngs), max(lats), max(lngs)) * 0.5, 3
            )
            geojson = {"type": "Polygon",
                       "coordinates": [[[p["lon"], p["lat"]] for p in geometry]]}
        else:
            area_km2 = 0.0
            geojson  = {}

        if name in existing:
            f = existing[name]
            f.risk_level    = risk_level
            f.risk_score    = score
            f.area_km2      = area_km2
            f.geojson       = geojson
            f.last_analyzed = now
            to_update.append(f)
        else:
            to_create.append(FloodRisk(
                zone=zone, name=name, risk_level=risk_level,
                risk_score=score, area_km2=area_km2,
                rainfall_mm=0.0,
                geojson=geojson, last_analyzed=now,
            ))

    with transaction.atomic():
        if to_create:
            FloodRisk.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            FloodRisk.objects.bulk_update(
                to_update,
                ["risk_level", "risk_score", "area_km2", "geojson", "last_analyzed"],
                batch_size=200,
            )

    return len(to_create), len(to_update)


def save_vegetation(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    now = timezone.now()
    existing = {
        v.name: v
        for v in VegetationDensity.objects.filter(zone=zone)
        .only("id", "name", "ndvi_value", "density_class",
              "coverage_percent", "geojson", "last_analyzed")
    }
    to_create, to_update = [], []

    for el in elements:
        tags    = el.get("tags", {})
        landuse = tags.get("landuse", "")
        natural = tags.get("natural", "")
        name    = tags.get("name") or f"Végétation #{el['id']}"
        key     = landuse or natural
        lo, hi  = NDVI_RANGE.get(key, (0.18, 0.55))
        ndvi    = round(lo + (el["id"] % 10000) / 10000.0 * (hi - lo), 3)
        density = ndvi_to_density(ndvi)

        geometry = el.get("geometry", [])
        geojson  = (
            {"type": "Polygon",
             "coordinates": [[[p["lon"], p["lat"]] for p in geometry]]}
            if geometry else {}
        )

        if name in existing:
            v = existing[name]
            v.ndvi_value       = ndvi
            v.density_class    = density
            v.coverage_percent = round(ndvi * 100, 1)
            v.geojson          = geojson
            v.last_analyzed    = now
            to_update.append(v)
        else:
            to_create.append(VegetationDensity(
                zone=zone, name=name, ndvi_value=ndvi,
                density_class=density, coverage_percent=round(ndvi * 100, 1),
                change_vs_previous=0.0,
                geojson=geojson, last_analyzed=now,
            ))

    with transaction.atomic():
        if to_create:
            VegetationDensity.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            VegetationDensity.objects.bulk_update(
                to_update,
                ["ndvi_value", "density_class", "coverage_percent",
                 "geojson", "last_analyzed"],
                batch_size=200,
            )

    return len(to_create), len(to_update)


def generate_alerts(zone: Zone) -> int:
    """Génère des alertes sans doublons via get_or_create."""
    count = 0
    now   = timezone.now()

    for road in zone.roads.filter(status__in=["critique", "ferme"]).order_by("condition_score")[:3]:
        _, created = Alert.objects.get_or_create(
            zone=zone, title=f"Route dégradée : {road.name}",
            category="road", is_read=False,
            defaults={
                "message": (
                    f"Segment '{road.name}' — score {road.condition_score}/100 "
                    f"({road.get_status_display()}). Inspection recommandée."
                ),
                "severity": "critical" if road.status == "ferme" else "danger",
                "created_at": now, "lat": zone.lat_center, "lng": zone.lng_center,
            },
        )
        if created:
            count += 1

    for flood in zone.flood_risks.filter(risk_level__in=["eleve", "critique"]).order_by("-risk_score")[:2]:
        _, created = Alert.objects.get_or_create(
            zone=zone, title=f"Risque inondation : {flood.name}",
            category="flood", is_read=False,
            defaults={
                "message": (
                    f"Zone '{flood.name}' — risque {flood.get_risk_level_display()}, "
                    f"score {flood.risk_score}/100."
                ),
                "severity": "critical" if flood.risk_level == "critique" else "warning",
                "created_at": now, "lat": zone.lat_center, "lng": zone.lng_center,
            },
        )
        if created:
            count += 1

    return count


# ─── Commande principale ──────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Importe des données géospatiales OSM pour toutes les zones de Côte d'Ivoire."

    def add_arguments(self, parser):
        parser.add_argument("--zone",       type=str,        default=None,
                            help="Code zone (ex: MAN). Absent = toutes.")
        parser.add_argument("--dry-run",    action="store_true",
                            help="Simule sans écrire en base.")
        parser.add_argument("--clear",      action="store_true",
                            help="Supprime les données existantes avant import.")
        parser.add_argument("--roads-only", action="store_true",
                            help="Routes uniquement.")

    def handle(self, *args, **options):
        dry_run    = options["dry_run"]
        zone_code  = options["zone"]
        roads_only = options["roads_only"]

        if dry_run:
            self.stdout.write(self.style.WARNING("⚠  DRY-RUN — aucune écriture en base\n"))

        # ── Création automatique des zones manquantes ──
        if not dry_run:
            self.stdout.write(self.style.HTTP_INFO("📍 Vérification des zones de Côte d'Ivoire..."))
            n = create_zones_if_missing(self.stdout)
            if n:
                self.stdout.write(self.style.SUCCESS(f"  ✓ {n} zone(s) créée(s)\n"))
            else:
                self.stdout.write(f"  ✓ {Zone.objects.count()} zones déjà présentes\n")

        # ── Sélection des zones ──
        if zone_code:
            zones = Zone.objects.filter(code__iexact=zone_code)
            if not zones.exists():
                available = ", ".join(Zone.objects.values_list("code", flat=True))
                raise CommandError(f"Zone '{zone_code}' introuvable. Codes : {available}")
        else:
            zones = Zone.objects.all()

        if not zones.exists():
            raise CommandError("Aucune zone en base. Lance sans --zone pour créer les zones CI.")

        self.stdout.write(f"Zones à traiter : {zones.count()}\n")

        # ── Nettoyage optionnel ──
        if options["clear"] and not dry_run:
            self.stdout.write(self.style.WARNING("🗑  Suppression des données existantes..."))
            with transaction.atomic():
                scope = zones if zone_code else None
                if scope:
                    for z in scope:
                        z.roads.all().delete()
                        z.flood_risks.all().delete()
                        z.vegetation.all().delete()
                        z.alerts.all().delete()
                else:
                    RoadSegment.objects.all().delete()
                    FloodRisk.objects.all().delete()
                    VegetationDensity.objects.all().delete()
                    Alert.objects.filter(category__in=["road", "flood", "vegetation"]).delete()
            self.stdout.write("  ✓ Base nettoyée\n")

        # ── Import zone par zone ──
        totals = dict(rc=0, ru=0, fc=0, fu=0, vc=0, vu=0, alerts=0, errors=0)

        for zone in zones:
            self.stdout.write(self.style.HTTP_INFO(f"\n{'─' * 52}"))
            self.stdout.write(self.style.HTTP_INFO(f"  {zone.name} ({zone.code})"))

            zone_bbox = make_bbox(zone.lat_center, zone.lng_center)

            # ── Une seule requête Overpass pour toute la zone ──
            self.stdout.write("  → Requête Overpass (routes + eau + végétation)...")
            data = overpass_fetch(zone_bbox, roads_only, self.stdout)

            if data is None:
                totals["errors"] += 1
                logger.error("Import échoué — zone %s (%s)", zone.name, zone.code)
                self.stdout.write(self.style.ERROR(
                    f"  ✗ Overpass inaccessible pour {zone.name} — zone ignorée."
                ))
                time.sleep(REQUEST_DELAY)
                continue

            # Classer les éléments en une passe
            elements = [el for el in data.get("elements", []) if el.get("type") == "way"]
            roads_el  = [el for el in elements if classify_element(el) == "road"]
            flood_el  = [el for el in elements if classify_element(el) == "flood"]
            veg_el    = [el for el in elements if classify_element(el) == "vegetation"]

            self.stdout.write(
                f"  ✓ {len(roads_el)} routes, {len(flood_el)} zones eau, "
                f"{len(veg_el)} zones végétation"
            )

            if dry_run:
                time.sleep(REQUEST_DELAY)
                continue

            # ── Persistance ──
            try:
                c, u = save_roads(zone, roads_el, self.stdout)
                totals["rc"] += c
                totals["ru"] += u
                self.stdout.write(f"  ✓ Routes sauvegardées : {c} créées, {u} mises à jour")
            except Exception as e:
                totals["errors"] += 1
                logger.exception("Erreur save_roads — zone %s", zone.code)
                self.stdout.write(self.style.ERROR(f"  ✗ Routes : {e}"))

            if not roads_only:
                try:
                    c, u = save_flood_risks(zone, flood_el, self.stdout)
                    totals["fc"] += c
                    totals["fu"] += u
                    self.stdout.write(f"  ✓ Inondations sauvegardées : {c} créées, {u} mises à jour")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur save_flood_risks — zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  ✗ Inondations : {e}"))

                try:
                    c, u = save_vegetation(zone, veg_el, self.stdout)
                    totals["vc"] += c
                    totals["vu"] += u
                    self.stdout.write(f"  ✓ Végétation sauvegardée : {c} créées, {u} mises à jour")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur save_vegetation — zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  ✗ Végétation : {e}"))

                try:
                    n = generate_alerts(zone)
                    totals["alerts"] += n
                    self.stdout.write(f"  ✓ Alertes générées : {n}")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur generate_alerts — zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  ✗ Alertes : {e}"))

            time.sleep(REQUEST_DELAY)

        # ── Résumé ──
        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 52}"))
        self.stdout.write(self.style.SUCCESS("Import terminé"))
        if not dry_run:
            self.stdout.write(f"  Routes      — créées : {totals['rc']}, mises à jour : {totals['ru']}")
            self.stdout.write(f"  Inondations — créées : {totals['fc']}, mises à jour : {totals['fu']}")
            self.stdout.write(f"  Végétation  — créées : {totals['vc']}, mises à jour : {totals['vu']}")
            self.stdout.write(f"  Alertes générées     : {totals['alerts']}")
            if totals["errors"]:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ {totals['errors']} zone(s) en erreur — consulte les logs Django."
                ))
        else:
            self.stdout.write(self.style.WARNING(
                "\n⚠  Dry-run terminé. Relance sans --dry-run pour importer."
            ))