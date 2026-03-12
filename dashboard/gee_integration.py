"""Earth Engine integration module for GéoDash.

Provides satellite imagery analysis (NDVI, flood detection, road surface quality)
via Google Earth Engine API with caching and error handling.

Functions cache results in Django cache backend to avoid repeated API calls.
All geometries use WGS84 (EPSG:4326) with bboxes as {west, south, east, north}.
"""

import json
import logging
from datetime import datetime, timedelta
from functools import wraps

import ee
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("geodash.gee")


# ─── Initialization (called once at Django startup) ───

_gee_initialized = False


def init_gee():
    """Initialize Earth Engine with service account credentials.
    
    Uses GEE_KEY_FILE and GEE_SERVICE_ACCOUNT from Django settings.
    Falls back to local credentials if service account is not configured.
    """
    global _gee_initialized
    if _gee_initialized:
        return

    try:
        key_file = getattr(settings, "GEE_KEY_FILE", None)
        svc_acct = getattr(settings, "GEE_SERVICE_ACCOUNT", None)

        if key_file and svc_acct:
            # Production: service account credentials from settings
            credentials = ee.ServiceAccountCredentials(str(svc_acct), str(key_file))
            ee.Initialize(credentials)
            logger.info("[GEE] Initialized with service account credentials.")
        else:
            # Development: use cached local credentials (requires prior ee.Authenticate())
            ee.Initialize()
            logger.info("[GEE] Initialized with local credentials.")

        _gee_initialized = True

    except Exception as exc:
        logger.error("[GEE] Initialization failed: %s", exc)
        raise


# ─── Caching decorator ───


def gee_cached(key_prefix, ttl=None):
    """Cache decorator for Earth Engine function results.
    
    Stores results in Django cache (Redis/Memcached) with TTL.
    Cache key includes function arguments to differentiate requests by zone/date.
    Reduces API quota usage by avoiding duplicate queries.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_ttl = ttl or getattr(settings, "GEE_CACHE_SECONDS", 3600)
            cache_key = f"gee:{key_prefix}:{hash(str(args) + str(sorted(kwargs.items())))}"

            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug("[GEE] Cache hit: %s", cache_key)
                return cached

            # Call Earth Engine function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, cache_ttl)
            logger.debug("[GEE] Cache miss — result cached for %ds: %s", cache_ttl, cache_key)
            return result
        return wrapper
    return decorator


# ─── NDVI — Vegetation index (Sentinel-2) ───
# Normalized Difference Vegetation Index for land cover assessment.
# Sentinel-2 12-day revisit, 10m GSD, cloud filtering < 20%.

@gee_cached("ndvi")
def get_ndvi_stats(bbox, days_back=30):
    """Compute vegetation index statistics for a region.
    
    Retrieves cloud-free Sentinel-2 imagery and calculates NDVI.
    Generates Web-Mercator tile URL for Leaflet visualization.
    
    Args:
        bbox: dict with keys {west, south, east, north} in WGS84 degrees
        days_back: number of days to look back (default 30)
    
    Returns:
        dict with keys:
            - mean_ndvi, min_ndvi, max_ndvi: NDVI statistics [-1, 1]
            - coverage_percent: vegetation coverage (NDVI > 0.2)
            - image_date: ISO date of source image
            - tiles_url: XYZ URL format for L.tileLayer in Leaflet
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    end_date   = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)

    # Query Sentinel-2 L2A (surface reflectance) with cloud filter (< 20%)
    # Harmonized collection ensures consistency across Sentinel-2A/B
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .sort("system:time_start", False)  # most recent first
    )

    image = collection.first()
    if image is None:
        logger.warning("[GEE NDVI] No cloud-free imagery available for %s", bbox)
        return None

    # NDVI = (NIR - RED) / (NIR + RED) using B8 (NIR) and B4 (RED)
    # Result range: [-1, 1] where > 0.3 indicates healthy vegetation
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

    # Reduce region to compute mean/min/max NDVI across study area
    # Scale 30m matches Sentinel-2 native resolution
    stats = ndvi.reduceRegion(
        reducer   = ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
        geometry  = region,
        scale     = 30,
        maxPixels = 1e9,  # allow full region computation
    ).getInfo()

    # Calculate vegetation coverage: pixels with NDVI > 0.2 = potential vegetation
    # Conservative threshold to avoid bare soil misclassification
    veg_mask    = ndvi.gt(0.2)
    total_px    = ndvi.reduceRegion(ee.Reducer.count(), region, 30, maxPixels=1e9).getInfo()["NDVI"]
    veg_px      = veg_mask.reduceRegion(ee.Reducer.sum(), region, 30, maxPixels=1e9).getInfo()["NDVI"]
    coverage_pct = round((veg_px / max(total_px, 1)) * 100, 1) if total_px else 0

    # Generate Web-Mercator tile layer URL for Leaflet
    # Palette: red (low NDVI) → yellow → green (high NDVI)
    viz_params = {"min": 0.0, "max": 0.8, "palette": ["#d73027", "#fee08b", "#1a9850"]}
    map_id     = ndvi.visualize(**viz_params).getMapId()
    tiles_url  = map_id["tile_fetcher"].url_format

    # Extract source image acquisition date
    ts = image.get("system:time_start").getInfo()
    image_date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")

    return {
        "mean_ndvi":        round(stats.get("NDVI_mean", 0), 4),
        "min_ndvi":         round(stats.get("NDVI_min", 0), 4),
        "max_ndvi":         round(stats.get("NDVI_max", 0), 4),
        "coverage_percent": coverage_pct,
        "image_date":       image_date,
        "tiles_url":        tiles_url,   # ← à injecter dans Leaflet comme TileLayer
    }


# ─── Flood Detection — SAR water mapping (Sentinel-1) ───
# Synthetic Aperture Radar: day/night and weather-independent detection.
# Change detection: backscatter decrease (-3dB) indicates standing water.

@gee_cached("flood_sar", ttl=1800)  # 30-min TTL: SAR data more frequent than Sentinel-2
def get_flood_extent(bbox, days_back=14):
    """Detect flooded areas using SAR change detection.
    
    Compares Sentinel-1 VV backscatter before/after study period.
    Backscatter drop > 3dB indicates open water/flooding.
    
    Args:
        bbox: dict with keys {west, south, east, north} in WGS84 degrees
        days_back: days to analyze (creates before/after windows)
    
    Returns:
        dict with keys:
            - flooded_area_km2: estimated inundated area
            - risk_score: 0-100 normalized by region area
            - risk_level: categorical assessment (faible/modere/eleve/critique)
            - tiles_url: XYZ URL for visualization
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    # Define temporal windows: before/after comparison for change detection
    # Before: 3x longer period to avoid temporary SAR speckle noise
    now        = datetime.utcnow()
    after_end  = now
    after_start = now - timedelta(days=days_back)
    before_start = now - timedelta(days=days_back * 3)
    before_end   = now - timedelta(days=days_back)

    def _sar_mean(start, end):
        """Helper: compute mean SAR backscatter (VV) for a date range.
        
        Filters to Interferometric Wide (IW) mode for consistent 10m resolution.
        """
        return (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(region)
            .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            .filter(ee.Filter.eq("instrumentMode", "IW"))  # Interferometric Wide mode
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))  # VV pol
            .select("VV")
            .mean()  # reduce to single image
        )

    # Compute mean SAR for before/after windows
    before = _sar_mean(before_start, before_end)
    after  = _sar_mean(after_start, after_end)

    # Change detection: backscatter decrease > 3dB typical of water surfaces
    # VV decreases over water due to specular reflection (smooth surface)
    diff       = after.subtract(before)
    flood_mask = diff.lt(-3).selfMask()  # mask pixels where diff < -3dB

    # Calculate flooded area: pixel_area converts pixel count to area (m²) then to km²
    area_img = flood_mask.multiply(ee.Image.pixelArea()).divide(1e6)  # m² → km²
    area_stats = area_img.reduceRegion(
        ee.Reducer.sum(), region, scale=10, maxPixels=1e9
    ).getInfo()
    flooded_km2 = round(area_stats.get("VV", 0), 2)

    # Risk score: normalize flooded area by region area (calibrated for Abidjan delta)
    # Assumes significant flooding = 10% of region area
    region_area_km2 = (bbox["east"] - bbox["west"]) * (bbox["north"] - bbox["south"]) * 12321
    ratio      = min(flooded_km2 / max(region_area_km2 * 0.1, 0.1), 1.0)
    risk_score = int(ratio * 100)

    # Categorize risk by score thresholds
    if risk_score < 25:   risk_level = "faible"
    elif risk_score < 50: risk_level = "modere"
    elif risk_score < 75: risk_level = "eleve"
    else:                  risk_level = "critique"

    # Generate tile URL for flooded areas visualization (blue overlay)
    viz   = flood_mask.visualize(palette=["#3b82f6"])  # blue for water
    mapid = viz.getMapId()
    tiles_url = mapid["tile_fetcher"].url_format

    return {
        "flooded_area_km2": flooded_km2,
        "risk_score":       risk_score,
        "risk_level":       risk_level,
        "tiles_url":        tiles_url,
    }


# ─── Road Surface Quality — NDWI-based proxy (Landsat 8) ───
# Use moisture/wet index as degradation indicator (not yet calibrated with field data).
# NDWI sensitive to surface water content; degraded roads may trap moisture.

@gee_cached("road_condition")
def get_road_surface_index(bbox, road_geometry_wkt=None):
    """Compute road surface quality proxy using moisture index.
    
    EXPERIMENTAL: Uses NDWI as degradation proxy. Requires field calibration.
    
    Args:
        bbox: dict with keys {west, south, east, north} in WGS84 degrees
        road_geometry_wkt: optional WKT linestring for buffer extraction
    
    Returns:
        dict with keys:
            - surface_index: 0-1 normalized score (1 = best condition)
            - score: 0-100 scale
            - quality: categorical (bon/degrade/critique)
    """
    init_gee()

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    # Query Landsat 8 L2 (surface reflectance): 30m resolution, 16-day revisit
    # Collection 2 physics-based surface reflectance with cloud masking
    img = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(
            (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
            datetime.utcnow().strftime("%Y-%m-%d"),
        )
        .filter(ee.Filter.lt("CLOUD_COVER", 30))
        .median()  # stack into single cloudfree composite
        .multiply(0.0000275).add(-0.2)  # Collection 2 scaling factors
    )

    # NDWI = (GREEN - NIR) / (GREEN + NIR): detects surface moisture
    # Negative NDWI values indicate dry surfaces (healthy roads prefer drier state)
    ndwi = img.normalizedDifference(["SR_B3", "SR_B5"])  # B3=Green, B5=NIR
    stats = ndwi.reduceRegion(
        ee.Reducer.mean(), region, scale=30, maxPixels=1e9
    ).getInfo()

    ndwi_mean = stats.get("nd", 0) or 0
    # Invert NDWI: more negative = drier = better road condition
    # Normalize to [0, 1] via linear transform
    surface_index = round(max(0, min(1, (-ndwi_mean + 0.5))), 3)

    # Classify surface quality by index threshold
    # Thresholds are empirical; field validation required for production
    if surface_index > 0.65:   quality = "bon"
    elif surface_index > 0.35: quality = "degrade"
    else:                       quality = "critique"

    return {
        "surface_index": surface_index,
        "score":         int(surface_index * 100),
        "quality":       quality,
    }


# ─── Integration example: usage in views.py ───

"""
# Dans dashboard/views.py :

from .gee_integration import get_ndvi_stats, get_flood_extent, get_road_surface_index

def dashboard_view(request):
    zone_code = request.GET.get("zone", "ABJ-N")
    zone = get_object_or_404(Zone, code=zone_code)

    bbox = {
        "west":  zone.bbox_west,
        "south": zone.bbox_south,
        "east":  zone.bbox_east,
        "north": zone.bbox_north,
    }

    # Données GEE (mises en cache automatiquement)
    try:
        ndvi_data  = get_ndvi_stats(bbox)
        flood_data = get_flood_extent(bbox)
    except Exception as e:
        logger.error("GEE error: %s", e)
        ndvi_data = flood_data = None

    # Si GEE retourne une tiles_url, l'injecter dans le contexte Django
    # → dans index.html : {% if ndvi_tiles_url %}
    #     L.tileLayer('{{ ndvi_tiles_url }}', {...}).addTo(map);
    #   {% endif %}

    context = {
        "zone":          zone,
        "ndvi_tiles_url":  ndvi_data["tiles_url"]  if ndvi_data  else None,
        "flood_tiles_url": flood_data["tiles_url"] if flood_data else None,
        "avg_ndvi":        ndvi_data["mean_ndvi"]  if ndvi_data  else 0,
        "flood_risk":      flood_data["risk_level"] if flood_data else "inconnu",
        # ... reste du contexte
    }
    return render(request, "dashboard/index.html", context)
"""


# ─── Async endpoints for dashboard tile layer updates ───

"""
# Dans dashboard/urls.py :
path("api/gee/ndvi/",  views.api_gee_ndvi,  name="api_gee_ndvi"),
path("api/gee/flood/", views.api_gee_flood, name="api_gee_flood"),

# Dans dashboard/views.py :
from django.http import JsonResponse
from .gee_integration import get_ndvi_stats, get_flood_extent

def api_gee_ndvi(request):
    zone_code = request.GET.get("zone", "ABJ-N")
    zone = get_object_or_404(Zone, code=zone_code)
    bbox = {"west": zone.bbox_west, "south": zone.bbox_south,
            "east": zone.bbox_east, "north": zone.bbox_north}
    data = get_ndvi_stats(bbox)
    return JsonResponse(data or {"error": "GEE indisponible"})

# Dans dashboard.js — polling toutes les heures :
function refreshGeeLayer(zoneCode) {
    fetch('/api/gee/ndvi/?zone=' + zoneCode)
        .then(r => r.json())
        .then(data => {
            if (data.tiles_url) {
                // Ajouter/remplacer la couche NDVI dynamique dans Leaflet
                if (window._geeNdviLayer) map.removeLayer(window._geeNdviLayer);
                window._geeNdviLayer = L.tileLayer(data.tiles_url, {
                    opacity: 0.65, maxZoom: 19
                }).addTo(map);
            }
        });
}
"""