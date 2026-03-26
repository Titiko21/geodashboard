"""Earth Engine integration module for GéoDash.

Provides satellite imagery analysis (NDVI, flood detection, road surface quality)
via Google Earth Engine API with caching and error handling.

Functions cache results in Django cache backend to avoid repeated API calls.
All geometries use WGS84 (EPSG:4326) with bboxes as {west, south, east, north}.
"""

import logging
from datetime import datetime, timedelta
from functools import wraps

import ee
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("geodash.gee")


# ─── Initialization ───────────────────────────────────────────────────────────

_gee_initialized = False


def init_gee():
    """Initialize Earth Engine with service account credentials."""
    global _gee_initialized
    if _gee_initialized:
        return

    try:
        key_file = getattr(settings, "GEE_KEY_FILE", None)
        svc_acct = getattr(settings, "GEE_SERVICE_ACCOUNT", None)

        if key_file and svc_acct:
            credentials = ee.ServiceAccountCredentials(str(svc_acct), str(key_file))
            ee.Initialize(credentials)
            logger.info("[GEE] Initialized with service account credentials.")
        else:
            ee.Initialize()
            logger.info("[GEE] Initialized with local credentials.")

        _gee_initialized = True

    except Exception as exc:
        logger.error("[GEE] Initialization failed: %s", exc)
        raise


# ─── Caching decorator ────────────────────────────────────────────────────────

def gee_cached(key_prefix, ttl=None):
    """Cache decorator for Earth Engine function results."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_ttl = ttl or getattr(settings, "GEE_CACHE_SECONDS", 3600)
            cache_key = f"gee:{key_prefix}:{hash(str(args) + str(sorted(kwargs.items())))}"

            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug("[GEE] Cache hit: %s", cache_key)
                return cached

            result = func(*args, **kwargs)
            if result is not None:
                cache.set(cache_key, result, cache_ttl)
                logger.debug("[GEE] Cached for %ds: %s", cache_ttl, cache_key)
            return result
        return wrapper
    return decorator


# ─── Helper : vérifie qu'une collection n'est pas vide ───────────────────────

def _collection_size(collection):
    """
    Retourne le nombre d'images dans une collection GEE.

    IMPORTANT : collection.first() renvoie toujours un objet ee.Image côté
    Python, même quand la collection est vide — il ne retourne JAMAIS None.
    Le seul moyen fiable de vérifier est d'appeler .size().getInfo().
    """
    try:
        return collection.size().getInfo()
    except Exception as exc:
        logger.warning("[GEE] Impossible de compter la collection : %s", exc)
        return 0


# ─── NDVI — Vegetation index (Sentinel-2) ────────────────────────────────────

@gee_cached("ndvi")
def get_ndvi_stats(bbox, days_back=30):
    """
    Compute NDVI statistics for a region.

    Si aucune image cloud-free n'est disponible dans la fenêtre days_back,
    la fenêtre est étendue automatiquement jusqu'à 90 jours avant d'abandonner.

    Returns:
        dict avec mean_ndvi, coverage_percent, image_date, tiles_url
        None si aucune image n'est trouvable
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    # Fenêtres temporelles à essayer en cas de collection vide
    windows = [days_back, 60, 90]

    collection = None
    used_days  = days_back

    for window in windows:
        end_date   = datetime.utcnow()
        start_date = end_date - timedelta(days=window)

        col = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .sort("system:time_start", False)
        )

        size = _collection_size(col)
        if size > 0:
            collection = col
            used_days  = window
            logger.info(
                "[GEE NDVI] %d image(s) trouvée(s) sur %d jours pour bbox %s",
                size, window, bbox,
            )
            break
        else:
            logger.warning(
                "[GEE NDVI] Aucune image cloud-free sur %d jours pour bbox %s — "
                "extension de la fenêtre temporelle.",
                window, bbox,
            )

    if collection is None:
        logger.error(
            "[GEE NDVI] Aucune image Sentinel-2 disponible même sur 90 jours pour bbox %s",
            bbox,
        )
        return None

    image = collection.first()

    # Calcul NDVI : (B8 NIR - B4 RED) / (B8 + B4)
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

    stats = ndvi.reduceRegion(
        reducer   = ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
        geometry  = region,
        scale     = 30,
        maxPixels = 1e9,
    ).getInfo()

    # Couverture végétale : pixels NDVI > 0.2
    veg_mask = ndvi.gt(0.2)
    total_px = ndvi.reduceRegion(
        ee.Reducer.count(), region, 30, maxPixels=1e9
    ).getInfo().get("NDVI", 0)
    veg_px   = veg_mask.reduceRegion(
        ee.Reducer.sum(), region, 30, maxPixels=1e9
    ).getInfo().get("NDVI", 0)
    coverage_pct = round((veg_px / max(total_px, 1)) * 100, 1)

    # URL tuiles pour Leaflet
    viz_params = {"min": 0.0, "max": 0.8, "palette": ["#d73027", "#fee08b", "#1a9850"]}
    map_id     = ndvi.visualize(**viz_params).getMapId()
    tiles_url  = map_id["tile_fetcher"].url_format

    # Date de l'image source
    ts         = image.get("system:time_start").getInfo()
    image_date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")

    return {
        "mean_ndvi":        round(stats.get("NDVI_mean") or 0, 4),
        "min_ndvi":         round(stats.get("NDVI_min")  or 0, 4),
        "max_ndvi":         round(stats.get("NDVI_max")  or 0, 4),
        "coverage_percent": coverage_pct,
        "image_date":       image_date,
        "days_used":        used_days,
        "tiles_url":        tiles_url,
    }


# ─── Flood Detection — SAR (Sentinel-1) ──────────────────────────────────────

@gee_cached("flood_sar", ttl=1800)
def get_flood_extent(bbox, days_back=14):
    """
    Detect flooded areas using SAR change detection (Sentinel-1 VV).

    Vérifie que les collections before/after ne sont pas vides avant
    d'appeler subtract() — évite l'erreur sur image nulle.

    Returns:
        dict avec flooded_area_km2, risk_score, risk_level, tiles_url
        None si données SAR insuffisantes
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    now          = datetime.utcnow()
    after_end    = now
    after_start  = now - timedelta(days=days_back)
    before_start = now - timedelta(days=days_back * 3)
    before_end   = now - timedelta(days=days_back)

    def _sar_collection(start, end):
        return (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(region)
            .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .select("VV")
        )

    col_before = _sar_collection(before_start, before_end)
    col_after  = _sar_collection(after_start, after_end)

    # Vérification explicite — collection.first() != None en Python GEE
    size_before = _collection_size(col_before)
    size_after  = _collection_size(col_after)

    if size_before == 0 or size_after == 0:
        logger.warning(
            "[GEE SAR] Données SAR insuffisantes pour bbox %s "
            "(before: %d images, after: %d images)",
            bbox, size_before, size_after,
        )
        return None

    before = col_before.mean()
    after  = col_after.mean()

    diff       = after.subtract(before)
    flood_mask = diff.lt(-3).selfMask()

    area_img   = flood_mask.multiply(ee.Image.pixelArea()).divide(1e6)
    area_stats = area_img.reduceRegion(
        ee.Reducer.sum(), region, scale=10, maxPixels=1e9
    ).getInfo()
    flooded_km2 = round(area_stats.get("VV") or 0, 2)

    region_area_km2 = (
        (bbox["east"] - bbox["west"]) * (bbox["north"] - bbox["south"]) * 12321
    )
    ratio      = min(flooded_km2 / max(region_area_km2 * 0.1, 0.1), 1.0)
    risk_score = int(ratio * 100)

    if risk_score < 25:   risk_level = "faible"
    elif risk_score < 50: risk_level = "modere"
    elif risk_score < 75: risk_level = "eleve"
    else:                  risk_level = "critique"

    viz       = flood_mask.visualize(palette=["#3b82f6"])
    mapid     = viz.getMapId()
    tiles_url = mapid["tile_fetcher"].url_format

    return {
        "flooded_area_km2": flooded_km2,
        "risk_score":       risk_score,
        "risk_level":       risk_level,
        "tiles_url":        tiles_url,
    }


# ─── Road Surface Quality — NDWI proxy (Landsat 8) ───────────────────────────

@gee_cached("road_condition")
def get_road_surface_index(bbox):
    """
    Compute road surface quality proxy using NDWI (moisture index).

    Returns:
        dict avec surface_index, score, quality
        None si pas de données Landsat disponibles
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(
            (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
            datetime.utcnow().strftime("%Y-%m-%d"),
        )
        .filter(ee.Filter.lt("CLOUD_COVER", 30))
    )

    # Même vérification que NDVI — collection.first() ne retourne jamais None
    if _collection_size(col) == 0:
        logger.warning("[GEE Road] Aucune image Landsat disponible pour bbox %s", bbox)
        return None

    img = col.median().multiply(0.0000275).add(-0.2)

    ndwi = img.normalizedDifference(["SR_B3", "SR_B5"])
    stats = ndwi.reduceRegion(
        ee.Reducer.mean(), region, scale=30, maxPixels=1e9
    ).getInfo()

    ndwi_mean     = stats.get("nd") or 0
    surface_index = round(max(0, min(1, (-ndwi_mean + 0.5))), 3)

    if surface_index > 0.65:   quality = "bon"
    elif surface_index > 0.35: quality = "degrade"
    else:                       quality = "critique"

    return {
        "surface_index": surface_index,
        "score":         int(surface_index * 100),
        "quality":       quality,
    }