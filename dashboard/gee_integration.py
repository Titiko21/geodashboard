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


# ─── Initialization ──────────────────────────────────────────────────────────

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

    # .strip() défensif : Compose ne nettoie pas les espaces autour des "=" du
    # .env, ce qui rend l'email du service account invalide silencieusement.
    key_file = (getattr(settings, "GEE_KEY_FILE", "") or "").strip()
    svc_acct = (getattr(settings, "GEE_SERVICE_ACCOUNT", "") or "").strip()

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
        # Avant : on tentait quand même. Maintenant on échoue vite — sans DNS,
        # ee.Initialize() boucle 5× via googleapiclient avec backoff, ce qui
        # bloque les requêtes utilisateur 30s+. Mieux : signaler clairement.
        _gee_error = (
            "DNS résolution Google échouée (vérifier la conf 'dns:' du conteneur "
            f"docker-compose). Détails : {dns_failures}"
        )
        logger.error("[GEE] %s", _gee_error)
        return

    project = (getattr(settings, "GEE_PROJECT", "") or "").strip()
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            credentials = ee.ServiceAccountCredentials(svc_acct, key_file)
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


# ─── Caching decorator ───────────────────────────────────────────────────────

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


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _collection_size(collection):
    """
    Retourne le nombre d'images dans une collection GEE.
    Retourne -1 en cas d'erreur (à distinguer de 0 = collection réellement vide).
    """
    try:
        return collection.size().getInfo()
    except Exception as exc:
        logger.error("[GEE] ERREUR collection.size() : %s", exc, exc_info=True)
        return -1


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
        logger.error("[GEE] ERREUR vérification bandes : %s", exc, exc_info=True)
        return False


def _to_ee_geometry(geom_or_bbox):
    """
    Convertit un argument hétérogène en ee.Geometry :
      - dict bbox legacy {west, south, east, north} → Rectangle
      - chaîne GeoJSON                              → parsé puis ee.Geometry
      - dict GeoJSON                                → ee.Geometry direct

    Renvoie None si conversion impossible.
    """
    import json as _json
    if geom_or_bbox is None:
        return None
    try:
        if isinstance(geom_or_bbox, dict):
            # Cas legacy bbox
            if {"west", "south", "east", "north"} <= set(geom_or_bbox.keys()):
                return ee.Geometry.Rectangle([
                    geom_or_bbox["west"], geom_or_bbox["south"],
                    geom_or_bbox["east"], geom_or_bbox["north"],
                ])
            # Cas GeoJSON dict
            return ee.Geometry(geom_or_bbox)
        if isinstance(geom_or_bbox, str):
            return ee.Geometry(_json.loads(geom_or_bbox))
    except Exception as exc:
        logger.warning("[GEE] _to_ee_geometry impossible : %s", exc)
    return None


# ─── NDVI — Sentinel-2 ───────────────────────────────────────────────────────

@gee_cached("ndvi_v2")
def get_ndvi_stats(geom_or_bbox, days_back=30):
    """
    NDVI statistics for a region.

    Accepte une bbox legacy, une chaîne GeoJSON, ou un dict GeoJSON.
    Renvoie un dict avec stats + tiles_url (image clippée sur la région), ou None.
    """
    init_gee()
    if not _gee_initialized:
        logger.error("[GEE NDVI] GEE non initialisé — skip")
        return None

    region = _to_ee_geometry(geom_or_bbox)
    if region is None:
        logger.warning("[GEE NDVI] géométrie invalide")
        return None

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
        if size < 0:
            logger.error("[GEE NDVI] Erreur collection Sentinel-2 (fenêtre %dj)", window)
            return None
        logger.info("[GEE NDVI] fenêtre=%dj → %d images", window, size)
        if size > 0:
            collection = col
            used_days = window
            break

    if collection is None:
        logger.warning("[GEE NDVI] Aucune image Sentinel-2 sur 30/60/90j")
        return None

    image = collection.first().select(["B8", "B4"])
    if not _check_bands(image, ["B8", "B4"], geom_or_bbox):
        return None

    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

    try:
        stats = ndvi.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
            geometry=region, scale=30, maxPixels=1e9,
        ).getInfo()
    except Exception as exc:
        logger.error("[GEE NDVI] ERREUR reduceRegion : %s", exc, exc_info=True)
        return None

    if stats.get("NDVI_mean") is None:
        logger.warning("[GEE NDVI] NDVI_mean None pour la région demandée")
        return None

    veg_mask = ndvi.gt(0.2)
    total_px = ndvi.reduceRegion(
        ee.Reducer.count(), region, 30, maxPixels=1e9
    ).getInfo().get("NDVI", 0)
    veg_px = veg_mask.reduceRegion(
        ee.Reducer.sum(), region, 30, maxPixels=1e9
    ).getInfo().get("NDVI", 0)
    coverage_pct = round((veg_px / max(total_px, 1)) * 100, 1)

    # Clip à la région pour que le tile overlay ne déborde pas au-delà
    # du polygone admin/zone sélectionné (plus jamais d'overlay carré 55 km).
    viz_params = {"min": 0.0, "max": 0.8, "palette": ["#d73027", "#fee08b", "#1a9850"]}
    map_id = ndvi.clip(region).visualize(**viz_params).getMapId()
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

@gee_cached("flood_sar_v2", ttl=1800)
def get_flood_extent(geom_or_bbox, days_back=14):
    """
    Detect flooded areas using SAR change detection.
    Accepte bbox legacy, GeoJSON string ou dict.
    """
    init_gee()
    if not _gee_initialized:
        logger.error("[GEE Flood] GEE non initialisé — skip")
        return None

    region = _to_ee_geometry(geom_or_bbox)
    if region is None:
        logger.warning("[GEE Flood] géométrie invalide")
        return None

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

    size_before = _collection_size(col_before)
    size_after = _collection_size(col_after)

    if size_before < 0 or size_after < 0:
        logger.error("[GEE Flood] Erreur collection SAR")
        return None

    logger.info("[GEE Flood] before=%d images, after=%d images", size_before, size_after)

    if size_before == 0 or size_after == 0:
        return None

    before = col_before.mean()
    after = col_after.mean()
    diff = after.subtract(before)
    flood_mask = diff.lt(-3).selfMask()

    try:
        area_img = flood_mask.multiply(ee.Image.pixelArea()).divide(1e6)
        area_stats = area_img.reduceRegion(
            ee.Reducer.sum(), region, scale=10, maxPixels=1e9
        ).getInfo()
    except Exception as exc:
        logger.error("[GEE Flood] ERREUR reduceRegion : %s", exc, exc_info=True)
        return None

    flooded_km2 = round(area_stats.get("VV") or 0, 2)

    # Surface réelle de la région (en km²) via GEE — fonctionne pour n'importe
    # quel polygone, pas juste les bbox rectangulaires.
    try:
        region_area_km2 = region.area().getInfo() / 1e6
    except Exception:
        region_area_km2 = 1000.0  # fallback safe
    ratio = min(flooded_km2 / max(region_area_km2 * 0.1, 0.1), 1.0)
    risk_score = int(ratio * 100)

    if risk_score < 25:    risk_level = "faible"
    elif risk_score < 50:  risk_level = "modere"
    elif risk_score < 75:  risk_level = "eleve"
    else:                  risk_level = "critique"

    # Clip à la région : l'overlay SAR ne déborde plus du polygone admin
    viz = flood_mask.clip(region).visualize(palette=["#3b82f6"])
    mapid = viz.getMapId()
    tiles_url = mapid["tile_fetcher"].url_format

    return {
        "flooded_area_km2": flooded_km2,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "tiles_url": tiles_url,
    }


# ─── Sonde altimétrique ponctuelle ───────────────────────────────────────────

@gee_cached("pt_elev_v1", ttl=60 * 60 * 24 * 30)
def get_point_elevation(lat, lng):
    """
    Altitude (Copernicus GLO-30) et hauteur au-dessus du drainage (HAND,
    MERIT Hydro) en un point. Coordonnées arrondies en amont pour le cache.

    Renvoie {"elevation_m", "hand_m"} (valeurs None si hors couverture),
    ou None si GEE indisponible.
    """
    init_gee()
    if not _gee_initialized:
        return None
    try:
        pt = ee.Geometry.Point([lng, lat])
        dem = (
            ee.ImageCollection("COPERNICUS/DEM/GLO30")
            .select("DEM").mosaic()
            .setDefaultProjection("EPSG:4326", None, 30)
        )
        hand = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")

        elev_f = dem.sample(pt, 30).first()
        hand_f = hand.sample(pt, 90).first()
        elev = ee.Algorithms.If(elev_f, ee.Feature(elev_f).get("DEM"), None)
        hnd = ee.Algorithms.If(hand_f, ee.Feature(hand_f).get("hnd"), None)
        values = ee.List([elev, hnd]).getInfo()

        return {
            "elevation_m": round(values[0], 1) if values[0] is not None else None,
            "hand_m":      round(values[1], 2) if values[1] is not None else None,
        }
    except Exception as exc:
        logger.error("[GEE Élévation] échec (%s, %s) : %s", lat, lng, exc)
        return None


# ─── Courbes de niveau — Copernicus GLO-30 ───────────────────────────────────

@gee_cached("contours_v1", ttl=60 * 60 * 24 * 7)
def get_contour_tiles(geom_or_bbox, interval=5):
    """
    Tuiles de courbes de niveau dérivées du MNT Copernicus GLO-30.

    Technique « changement de classe » : on classe chaque pixel par tranche
    d'altitude (floor(alt/intervalle)) et on ne garde que les pixels où la
    classe change par rapport au voisinage → lignes fines et régulières,
    y compris en terrain plat (le simple modulo y produirait des nappes).

    Courbes fines tous les `interval` m (brun clair) + courbes maîtresses
    tous les `interval*5` m (brun foncé, épaissies). Renvoie
    {"tiles_url", "interval", "major_interval"} ou None.
    """
    init_gee()
    if not _gee_initialized:
        return None
    region = _to_ee_geometry(geom_or_bbox)
    if region is None:
        return None

    try:
        dem = (
            ee.ImageCollection("COPERNICUS/DEM/GLO30")
            .select("DEM").mosaic()
            .setDefaultProjection("EPSG:4326", None, 30)
        )

        def _edges(iv):
            classed = dem.divide(iv).floor()
            return classed.subtract(classed.focalMin(1)).gt(0)

        minor = _edges(interval).selfMask().visualize(palette=["#b08968"])
        major = (
            _edges(interval * 5).focalMax(1).selfMask()
            .visualize(palette=["#5c4030"])
        )
        combined = ee.ImageCollection([minor, major]).mosaic().clip(region)
        tiles_url = combined.getMapId()["tile_fetcher"].url_format
        return {
            "tiles_url":      tiles_url,
            "interval":       interval,
            "major_interval": interval * 5,
        }
    except Exception as exc:
        logger.error("[GEE Contours] échec : %s", exc, exc_info=True)
        return None


# ─── Fond de carte GEE — imagerie Sentinel-2 ─────────────────────────────────
#
# Fond « Satellite GEE » du frontend : composite Sentinel-2 vraie couleur.
# Contrairement aux analyses (NDVI, SAR…), le fond n'est PAS clippé sur une
# emprise : GEE calcule chaque tuile à la demande, où que l'on navigue.

@gee_cached("gee_basemap_v1", ttl=60 * 60 * 24)
def get_gee_basemap():
    """
    Tuiles du fond satellite GEE.
    Renvoie {"imagery_tiles_url"} ou None si GEE indisponible.
    """
    init_gee()
    if not _gee_initialized:
        return None
    try:
        # Médiane Sentinel-2 des 12 derniers mois (nuages < 20 %).
        # 12 mois : en zone tropicale il faut traverser la saison des pluies
        # pour que chaque pixel ait des observations claires.
        end = datetime.utcnow()
        start = end - timedelta(days=365)
        imagery = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .select(["B4", "B3", "B2"])
            .median()
            .visualize(min=0, max=3000, gamma=1.2)
        )
        imagery_url = imagery.getMapId()["tile_fetcher"].url_format
        return {"imagery_tiles_url": imagery_url}
    except Exception as exc:
        logger.error("[GEE Basemap] échec : %s", exc, exc_info=True)
        return None


# ─── Road Surface Quality — Landsat 8 ────────────────────────────────────────

@gee_cached("road_condition_v2")
def get_road_surface_index(geom_or_bbox):
    """
    Road surface quality proxy using NDWI.
    Accepte bbox legacy, GeoJSON string ou dict.
    """
    init_gee()
    if not _gee_initialized:
        return None

    region = _to_ee_geometry(geom_or_bbox)
    if region is None:
        return None

    col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(
            (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
            datetime.utcnow().strftime("%Y-%m-%d"),
        )
        .filter(ee.Filter.lt("CLOUD_COVER", 30))
    )

    size = _collection_size(col)
    if size <= 0:
        return None

    img = col.median().select(["SR_B3", "SR_B5"]).multiply(0.0000275).add(-0.2)
    if not _check_bands(img, ["SR_B3", "SR_B5"], geom_or_bbox):
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


# ─── Land Use Breakdown — Google Dynamic World ──────────────────────────────
#
# Dynamic World est un modèle Google qui classifie chaque image Sentinel-2
# en 9 catégories d'occupation des sols, en quasi-temps réel (nouvelle image
# tous les 2-5 jours selon la couverture nuageuse).
#
# Pour une vue stable d'une zone, on prend le MODE (classe la plus fréquente
# par pixel) sur une fenêtre glissante des derniers mois. Cela lisse les
# anomalies temporaires (ombres, classifications ambiguës) tout en restant
# représentatif de l'occupation actuelle.
#
# Classes Dynamic World :
#   0 water · 1 trees · 2 grass · 3 flooded_vegetation · 4 crops
#   5 shrub_and_scrub · 6 built · 7 bare · 8 snow_and_ice
#
# Mapping vers les 5 catégories produit :

_DYNAMIC_WORLD_BUCKETS = {
    "urban":    [6],            # built
    "cropland": [4, 2],         # crops + grass
    "forest":   [1, 5, 3],      # trees + shrub_and_scrub + flooded_vegetation
    "water":    [0],            # water
    "bare":     [7, 8],         # bare + snow_and_ice
}

# Fenêtre d'analyse (en jours). 180 j = 6 mois : compromis fraîcheur / stabilité.
# Pendant la saison des pluies les nuages sont fréquents → on a besoin de
# plusieurs mois pour cumuler assez d'observations claires sur chaque pixel.
DYNAMIC_WORLD_WINDOW_DAYS = 180


@gee_cached("landuse_dw_v3", ttl=60 * 60 * 24 * 7)
def get_land_use_breakdown(geom_geojson):
    """
    Calcule la distribution d'occupation des sols sur une géométrie polygone.

    Source : Google Dynamic World (10 m, ~temps réel via Sentinel-2).
    Fenêtre : mode des classifications sur les 6 derniers mois.

    Renvoie un dict {urban, cropland, forest, water, bare, source, period_end}
    en pourcentages arrondis. None si GEE indisponible ou requête échouée.
    """
    init_gee()
    if not _gee_initialized:
        return None
    if not geom_geojson:
        return None

    try:
        if isinstance(geom_geojson, str):
            import json as _json
            geom_geojson = _json.loads(geom_geojson)
        region = ee.Geometry(geom_geojson)

        end = datetime.utcnow()
        start = end - timedelta(days=DYNAMIC_WORLD_WINDOW_DAYS)

        # Sélectionne uniquement la bande 'label' (classification entière 0-8).
        # Filtrage spatial + temporel pour ne charger que ce qui couvre la zone.
        dw = (
            ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
            .filterBounds(region)
            .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            .select("label")
        )

        if _collection_size(dw) == 0:
            return None

        # Mode (classe la plus fréquente par pixel) sur la période.
        # Lisse les anomalies ponctuelles et révèle l'occupation dominante.
        classification = dw.reduce(ee.Reducer.mode())

        # Histogramme global sur la région.
        hist = classification.reduceRegion(
            reducer=ee.Reducer.frequencyHistogram(),
            geometry=region,
            scale=100,
            maxPixels=1e9,
        ).get("label_mode").getInfo()

        if not hist:
            return None

        # Les clés du dict GEE peuvent venir en string "0", "1.0", etc.
        normalized = {}
        for k, v in hist.items():
            try:
                key = int(float(k))
                normalized[key] = normalized.get(key, 0) + v
            except (ValueError, TypeError):
                continue

        total = sum(normalized.values())
        if total == 0:
            return None

        def sum_classes(class_ids):
            return sum(normalized.get(c, 0) for c in class_ids)

        result = {
            bucket: round(sum_classes(classes) / total * 100, 1)
            for bucket, classes in _DYNAMIC_WORLD_BUCKETS.items()
        }
        result["source"] = f"Google Dynamic World · mode 6 mois"
        result["period_end"] = end.strftime("%Y-%m-%d")
        return result

    except Exception as exc:
        logger.warning("[GEE LandUse] échec calcul : %s", exc)
        return None