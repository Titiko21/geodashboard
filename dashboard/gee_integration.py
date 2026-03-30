"""
gee_integration.py — GéoDash
Module unifié Google Earth Engine.

Fournit :
  - Initialisation robuste avec retry + vérification DNS
  - get_ee() — accès simple au module ee initialisé
  - get_ndvi_stats() — NDVI Sentinel-2
  - get_flood_extent() — détection inondation SAR Sentinel-1
  - get_road_surface_index() — qualité surface routière Landsat 8
  - gee_health_status() — diagnostic pour le health check
"""

import logging
import os
import socket
import time
from datetime import datetime, timedelta
from functools import wraps

import ee
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("geodash.gee")


# ─── Initialization ───────────────────────────────────────────────────────────

_gee_initialized = False
_gee_error = None

GEE_DNS_HOSTS = [
    "earthengine.googleapis.com",
    "oauth2.googleapis.com",
]


def _check_dns_resolution() -> list[str]:
    """Vérifie la résolution DNS des services Google nécessaires à GEE."""
    failures = []
    for host in GEE_DNS_HOSTS:
        try:
            socket.setdefaulttimeout(5)
            socket.getaddrinfo(host, 443)
        except socket.gaierror as e:
            failures.append(f"{host}: {e}")
            logger.warning("DNS resolution failed for %s: %s", host, e)
    return failures


def init_gee():
    """Initialize Earth Engine avec retry et gestion d'erreurs DNS."""
    global _gee_initialized, _gee_error
    if _gee_initialized:
        return

    key_file = getattr(settings, "GEE_KEY_FILE", "") or ""
    svc_acct = getattr(settings, "GEE_SERVICE_ACCOUNT", "") or ""

    if not svc_acct or not key_file:
        _gee_error = "GEE_SERVICE_ACCOUNT ou GEE_KEY_FILE non configuré"
        logger.warning("[GEE] Désactivé : %s", _gee_error)
        return

    if not os.path.isfile(key_file):
        _gee_error = f"Fichier clé GEE introuvable : {key_file}"
        logger.error("[GEE] %s", _gee_error)
        return

    dns_failures = _check_dns_resolution()
    if dns_failures:
        logger.warning("[GEE] DNS issues: %s — tentative quand même", dns_failures)

    project = getattr(settings, "GEE_PROJECT", "") or ""
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            credentials = ee.ServiceAccountCredentials(str(svc_acct), str(key_file))
            init_kwargs = {"credentials": credentials}
            if project:
                init_kwargs["project"] = project
            ee.Initialize(**init_kwargs)

            ee.Number(1).getInfo()

            _gee_initialized = True
            _gee_error = None
            logger.info("[GEE] Initialisé avec succès (tentative %d/%d)", attempt, max_retries)
            return

        except Exception as exc:
            _gee_error = str(exc)
            logger.warning("[GEE] Init failed (tentative %d/%d): %s", attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(5 * attempt)

    logger.error("[GEE] Inaccessible après %d tentatives : %s", max_retries, _gee_error)


def get_ee():
    """
    Retourne le module ee initialisé, ou None si GEE est indisponible.

    Usage :
        ee = get_ee()
        if ee is None:
            return  # fallback sans GEE
        image = ee.Image(...)
    """
    if not _gee_initialized:
        init_gee()
    if _gee_initialized:
        return ee
    return None


def is_gee_available() -> bool:
    """Vérifie si GEE est disponible sans tenter de réinitialiser."""
    return _gee_initialized


def get_gee_error() -> str | None:
    """Retourne la dernière erreur GEE, ou None si tout va bien."""
    return _gee_error


def gee_health_status() -> dict:
    """Retourne un dict de statut complet pour le health check."""
    dns_issues = _check_dns_resolution()
    key_file = getattr(settings, "GEE_KEY_FILE", "") or ""
    return {
        "initialized": _gee_initialized,
        "error": _gee_error,
        "dns_ok": len(dns_issues) == 0,
        "dns_failures": dns_issues,
        "service_account": bool(getattr(settings, "GEE_SERVICE_ACCOUNT", "")),
        "key_file_exists": os.path.isfile(key_file) if key_file else False,
        "project": getattr(settings, "GEE_PROJECT", ""),
    }


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
            return result
        return wrapper
    return decorator


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _collection_size(collection):
    """Retourne le nombre d'images dans une collection GEE."""
    try:
        return collection.size().getInfo()
    except Exception as exc:
        logger.warning("[GEE] Impossible de compter la collection : %s", exc)
        return 0


def _check_bands(image, required_bands, bbox):
    """Vérifie que l'image possède les bandes requises pour la région."""
    try:
        band_names = image.bandNames().getInfo()
        if not band_names:
            return False
        missing = [b for b in required_bands if b not in band_names]
        if missing:
            logger.warning("[GEE] Bandes manquantes %s pour bbox %s", missing, bbox)
            return False
        return True
    except Exception as exc:
        logger.warning("[GEE] Impossible de vérifier les bandes : %s", exc)
        return False


# ─── NDVI — Sentinel-2 ───────────────────────────────────────────────────────

@gee_cached("ndvi")
def get_ndvi_stats(bbox, days_back=30):
    """
    NDVI statistics for a region.
    Returns dict or None.
    """
    init_gee()
    if not _gee_initialized:
        return None

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    windows = [days_back, 60, 90]
    collection = None
    used_days = days_back

    for window in windows:
        end_date = datetime.utcnow()
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
            used_days = window
            break

    if collection is None:
        return None

    image = collection.first().select(["B8", "B4"])
    if not _check_bands(image, ["B8", "B4"], bbox):
        return None

    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

    stats = ndvi.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
        geometry=region, scale=30, maxPixels=1e9,
    ).getInfo()

    if stats.get("NDVI_mean") is None:
        return None

    veg_mask = ndvi.gt(0.2)
    total_px = ndvi.reduceRegion(
        ee.Reducer.count(), region, 30, maxPixels=1e9
    ).getInfo().get("NDVI", 0)
    veg_px = veg_mask.reduceRegion(
        ee.Reducer.sum(), region, 30, maxPixels=1e9
    ).getInfo().get("NDVI", 0)
    coverage_pct = round((veg_px / max(total_px, 1)) * 100, 1)

    viz_params = {"min": 0.0, "max": 0.8, "palette": ["#d73027", "#fee08b", "#1a9850"]}
    map_id = ndvi.visualize(**viz_params).getMapId()
    tiles_url = map_id["tile_fetcher"].url_format

    ts = collection.first().get("system:time_start").getInfo()
    image_date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")

    return {
        "mean_ndvi": round(stats.get("NDVI_mean") or 0, 4),
        "min_ndvi": round(stats.get("NDVI_min") or 0, 4),
        "max_ndvi": round(stats.get("NDVI_max") or 0, 4),
        "coverage_percent": coverage_pct,
        "image_date": image_date,
        "days_used": used_days,
        "tiles_url": tiles_url,
    }


# ─── Flood Detection — SAR Sentinel-1 ────────────────────────────────────────

@gee_cached("flood_sar", ttl=1800)
def get_flood_extent(bbox, days_back=14):
    """
    Detect flooded areas using SAR change detection.
    Returns dict or None.
    """
    init_gee()
    if not _gee_initialized:
        return None

    region = ee.Geometry.Rectangle([
        bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    ])

    now = datetime.utcnow()
    after_end = now
    after_start = now - timedelta(days=days_back)
    before_start = now - timedelta(days=days_back * 3)
    before_end = now - timedelta(days=days_back)

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
    col_after = _sar_collection(after_start, after_end)

    if _collection_size(col_before) == 0 or _collection_size(col_after) == 0:
        return None

    before = col_before.mean()
    after = col_after.mean()
    diff = after.subtract(before)
    flood_mask = diff.lt(-3).selfMask()

    area_img = flood_mask.multiply(ee.Image.pixelArea()).divide(1e6)
    area_stats = area_img.reduceRegion(
        ee.Reducer.sum(), region, scale=10, maxPixels=1e9
    ).getInfo()
    flooded_km2 = round(area_stats.get("VV") or 0, 2)

    region_area_km2 = (
        (bbox["east"] - bbox["west"]) * (bbox["north"] - bbox["south"]) * 12321
    )
    ratio = min(flooded_km2 / max(region_area_km2 * 0.1, 0.1), 1.0)
    risk_score = int(ratio * 100)

    if risk_score < 25:    risk_level = "faible"
    elif risk_score < 50:  risk_level = "modere"
    elif risk_score < 75:  risk_level = "eleve"
    else:                  risk_level = "critique"

    viz = flood_mask.visualize(palette=["#3b82f6"])
    mapid = viz.getMapId()
    tiles_url = mapid["tile_fetcher"].url_format

    return {
        "flooded_area_km2": flooded_km2,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "tiles_url": tiles_url,
    }


# ─── Road Surface Quality — Landsat 8 ────────────────────────────────────────

@gee_cached("road_condition")
def get_road_surface_index(bbox):
    """
    Road surface quality proxy using NDWI.
    Returns dict or None.
    """
    init_gee()
    if not _gee_initialized:
        return None

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

    if _collection_size(col) == 0:
        return None

    img = col.median().select(["SR_B3", "SR_B5"]).multiply(0.0000275).add(-0.2)
    if not _check_bands(img, ["SR_B3", "SR_B5"], bbox):
        return None

    ndwi = img.normalizedDifference(["SR_B3", "SR_B5"])
    stats = ndwi.reduceRegion(
        ee.Reducer.mean(), region, scale=30, maxPixels=1e9
    ).getInfo()

    ndwi_mean = stats.get("nd")
    if ndwi_mean is None:
        return None

    surface_index = round(max(0, min(1, (-ndwi_mean + 0.5))), 3)

    if surface_index > 0.65:    quality = "bon"
    elif surface_index > 0.35:  quality = "degrade"
    else:                       quality = "critique"

    return {
        "surface_index": surface_index,
        "score": int(surface_index * 100),
        "quality": quality,
    }