import logging
from datetime import datetime, timedelta
from functools import wraps

import ee
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("geodash.gee")


# ──────────────────────────────────────────────────────────────
# INITIALISATION
# ──────────────────────────────────────────────────────────────

_gee_initialized = False


def init_gee():
    """
    Initialise Earth Engine.
    - En production  : compte de service (GEE_SERVICE_ACCOUNT + GEE_KEY_FILE)
    - En développement : credentials locaux (ee.Authenticate() préalable)
      + GEE_PROJECT obligatoire dans .env
    """
    global _gee_initialized
    if _gee_initialized:
        return

    try:
        key_file = getattr(settings, "GEE_KEY_FILE", None)
        svc_acct = getattr(settings, "GEE_SERVICE_ACCOUNT", None)
        project  = getattr(settings, "GEE_PROJECT", None) or None

        if key_file and svc_acct:
            # ── Production : compte de service ──────────────────
            credentials = ee.ServiceAccountCredentials(str(svc_acct), str(key_file))
            ee.Initialize(credentials, project=project)
            logger.info("[GEE] Initialisé avec compte de service. Projet : %s", project)
        else:
            # ── Développement : credentials locaux ──────────────
            # Nécessite : python -c "import ee; ee.Authenticate()" une seule fois
            ee.Initialize(project=project)
            logger.info("[GEE] Initialisé avec credentials locaux. Projet : %s", project)

        _gee_initialized = True

    except Exception as exc:
        logger.error("[GEE] Échec initialisation : %s", exc)
        raise


# ──────────────────────────────────────────────────────────────
# CACHE DECORATOR
# ──────────────────────────────────────────────────────────────

def gee_cached(key_prefix, ttl=None):
    """
    Décorateur cache Django (RAM/Redis/Memcached selon CACHES dans settings).
    La clé inclut les arguments pour différencier les zones géographiques.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_ttl = ttl or getattr(settings, "GEE_CACHE_SECONDS", 3600)
            cache_key = (
                f"gee:{key_prefix}:"
                f"{hash(str(args) + str(sorted(kwargs.items())))}"
            )
            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug("[GEE] Cache hit : %s", cache_key)
                return cached

            result = func(*args, **kwargs)
            if result is not None:
                cache.set(cache_key, result, cache_ttl)
                logger.debug("[GEE] Cache miss — résultat stocké : %s", cache_key)
            return result
        return wrapper
    return decorator


# ──────────────────────────────────────────────────────────────
# NDVI — Index de végétation (Sentinel-2)
# ──────────────────────────────────────────────────────────────

@gee_cached("ndvi")
def get_ndvi_stats(bbox, days_back=30):
    """
    Calcule le NDVI moyen sur une zone et une période donnée.

    Args:
        bbox      : dict {west, south, east, north} en degrés décimaux
        days_back : nombre de jours en arrière (défaut 30)

    Returns:
        dict : {
            mean_ndvi        : float,
            min_ndvi         : float,
            max_ndvi         : float,
            coverage_percent : float,
            image_date       : str  (YYYY-MM-DD),
            tiles_url        : str  (URL XYZ pour Leaflet),
        }
        ou None si aucune image disponible.
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    end_date   = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)

    # Sentinel-2 SR harmonisé — filtre nuages < 20 %
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .sort("system:time_start", False)   # image la plus récente en premier
    )

    size = collection.size().getInfo()
    if size == 0:
        logger.warning("[GEE NDVI] Aucune image disponible pour bbox=%s", bbox)
        return None

    image = collection.first()

    # NDVI = (NIR - Rouge) / (NIR + Rouge) — bandes B8 (NIR) et B4 (Rouge)
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

    stats = ndvi.reduceRegion(
        reducer   = ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
        geometry  = region,
        scale     = 30,
        maxPixels = 1e9,
    ).getInfo()

    # Couverture végétale : pixels NDVI > 0.2
    veg_mask = ndvi.gt(0.2)
    total_px = ndvi.reduceRegion(ee.Reducer.count(), region, 30, maxPixels=1e9).getInfo().get("NDVI", 0)
    veg_px   = veg_mask.reduceRegion(ee.Reducer.sum(),   region, 30, maxPixels=1e9).getInfo().get("NDVI", 0)
    coverage_pct = round((veg_px / max(total_px, 1)) * 100, 1)

    # URL XYZ visualisation Leaflet
    viz_params = {"min": 0, "max": 0.8, "palette": ["#d73027", "#fee08b", "#1a9850"]}
    map_id    = ndvi.visualize(**viz_params).getMapId()
    tiles_url = map_id["tile_fetcher"].url_format

    # Date de l'image source
    ts         = image.get("system:time_start").getInfo()
    image_date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")

    return {
        "mean_ndvi":        round(stats.get("NDVI_mean", 0) or 0, 4),
        "min_ndvi":         round(stats.get("NDVI_min",  0) or 0, 4),
        "max_ndvi":         round(stats.get("NDVI_max",  0) or 0, 4),
        "coverage_percent": coverage_pct,
        "image_date":       image_date,
        "tiles_url":        tiles_url,
    }


# ──────────────────────────────────────────────────────────────
# RISQUE INONDATION — SAR Sentinel-1
# ──────────────────────────────────────────────────────────────

@gee_cached("flood_sar", ttl=1800)   # 30 min : données SAR plus fréquentes
def get_flood_extent(bbox, days_back=14):
    """
    Détecte les zones inondées par comparaison SAR avant/après.

    Args:
        bbox      : dict {west, south, east, north}
        days_back : fenêtre temporelle "après" (défaut 14 j)

    Returns:
        dict : {
            flooded_area_km2 : float,
            risk_score       : int (0-100),
            risk_level       : 'faible'|'modere'|'eleve'|'critique',
            tiles_url        : str (URL XYZ),
        }
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    now          = datetime.utcnow()
    after_start  = now - timedelta(days=days_back)
    before_start = now - timedelta(days=days_back * 3)
    before_end   = now - timedelta(days=days_back)

    def _sar_mean(start, end):
        return (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(region)
            .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .select("VV")
            .mean()
        )

    before = _sar_mean(before_start, before_end)
    after  = _sar_mean(after_start, now)

    # Différence : baisse VV > 3 dB → eau libre probable
    diff       = after.subtract(before)
    flood_mask = diff.lt(-3).selfMask()

    # Surface inondée en km²
    area_img   = flood_mask.multiply(ee.Image.pixelArea()).divide(1e6)
    area_stats = area_img.reduceRegion(ee.Reducer.sum(), region, scale=10, maxPixels=1e9).getInfo()
    flooded_km2 = round(area_stats.get("VV", 0) or 0, 2)

    # Score de risque 0-100 proportionnel à la surface
    region_area_km2 = (bbox["east"] - bbox["west"]) * (bbox["north"] - bbox["south"]) * 12321
    ratio       = min(flooded_km2 / max(region_area_km2 * 0.1, 0.1), 1.0)
    risk_score  = int(ratio * 100)

    if risk_score < 25:   risk_level = "faible"
    elif risk_score < 50: risk_level = "modere"
    elif risk_score < 75: risk_level = "eleve"
    else:                  risk_level = "critique"

    viz       = flood_mask.visualize(palette=["#3b82f6"])
    tiles_url = viz.getMapId()["tile_fetcher"].url_format

    return {
        "flooded_area_km2": flooded_km2,
        "risk_score":       risk_score,
        "risk_level":       risk_level,
        "tiles_url":        tiles_url,
    }


# ──────────────────────────────────────────────────────────────
# ÉTAT DES ROUTES — proxy humidité surface (Landsat 8)
# ──────────────────────────────────────────────────────────────

@gee_cached("road_condition")
def get_road_surface_index(bbox):
    """
    Proxy de l'état de surface routière via NDWI Landsat 8.

    Returns:
        dict : { surface_index: float [0-1], score: int, quality: str }
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    img = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(
            (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
            datetime.utcnow().strftime("%Y-%m-%d"),
        )
        .filter(ee.Filter.lt("CLOUD_COVER", 30))
        .median()
        .multiply(0.0000275).add(-0.2)   # Facteur d'échelle Collection 2
    )

    # NDWI = (Green - NIR) / (Green + NIR)
    ndwi  = img.normalizedDifference(["SR_B3", "SR_B5"])
    stats = ndwi.reduceRegion(ee.Reducer.mean(), region, scale=30, maxPixels=1e9).getInfo()

    ndwi_mean     = stats.get("nd", 0) or 0
    surface_index = round(max(0, min(1, (-ndwi_mean + 0.5))), 3)

    if surface_index > 0.65:   quality = "bon"
    elif surface_index > 0.35: quality = "degrade"
    else:                       quality = "critique"

    return {
        "surface_index": surface_index,
        "score":         int(surface_index * 100),
        "quality":       quality,
    }
