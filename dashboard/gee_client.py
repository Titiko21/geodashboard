"""
gee_client.py — Connexion et requêtes Google Earth Engine
===========================================================
Ce module gère :
  - L'authentification via compte de service (private-key.json)
  - Le calcul NDVI (végétation) via Sentinel-2
  - Le risque d'inondation via SAR Sentinel-1
  - L'état des routes via détection de changement
  - La génération de GeoJSON pour la carte
"""

import ee
import threading
from django.conf import settings


# ── Initialisation unique (thread-safe) ────────────────────
_initialized = False
_init_lock   = threading.Lock()

def init_gee():
    """
    Initialise la connexion GEE une seule fois par session.
    Utilise les credentials du fichier private-key.json.
    Thread-safe grâce au double-check locking.
    """
    global _initialized
    if _initialized:
        return True

    with _init_lock:
        # Double-check : un autre thread a pu initialiser entre temps
        if _initialized:
            return True

        if not settings.GEE_SERVICE_ACCOUNT or not settings.GEE_KEY_FILE:
            raise ValueError(
                "GEE non configuré. Vérifiez GEE_SERVICE_ACCOUNT et GEE_KEY_FILE dans .env"
            )

        try:
            credentials = ee.ServiceAccountCredentials(
                email    = settings.GEE_SERVICE_ACCOUNT,
                key_file = settings.GEE_KEY_FILE,
            )
            # Utiliser le projet si défini, sinon laisser GEE le déduire
            if settings.GEE_PROJECT:
                ee.Initialize(credentials, project=settings.GEE_PROJECT)
            else:
                ee.Initialize(credentials)

            _initialized = True
            return True

        except Exception as e:
            raise ConnectionError(f"Échec d'authentification GEE : {e}")


def test_connection():
    """
    Teste la connexion GEE.
    Retourne (True, message) ou (False, message_erreur).
    """
    try:
        init_gee()
        # Requête simple pour valider la connexion
        info = ee.Image(1).getInfo()
        return True, "Connexion GEE réussie ✓"
    except Exception as e:
        return False, f"Erreur : {e}"


# ── Utilitaires GeoJSON ────────────────────────────────────

def bbox_to_geojson(lon_min, lat_min, lon_max, lat_max):
    """Convertit une bounding box en GeoJSON Polygon."""
    return {
        'type': 'Polygon',
        'coordinates': [[
            [lon_min, lat_min],
            [lon_max, lat_min],
            [lon_max, lat_max],
            [lon_min, lat_max],
            [lon_min, lat_min],
        ]]
    }

def bbox_to_ee_geometry(lon_min, lat_min, lon_max, lat_max):
    """Convertit une bounding box en objet ee.Geometry."""
    return ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max])


# ── Analyse NDVI — Végétation ──────────────────────────────

def get_ndvi_stats(lon_min, lat_min, lon_max, lat_max,
                   date_start='2025-01-01', date_end='2025-12-31'):
    """
    Calcule les statistiques NDVI sur une zone via Sentinel-2.

    Retourne un dict :
      {
        'ndvi_mean':    float,   # NDVI moyen (-1 à 1)
        'ndvi_max':     float,
        'coverage_pct': float,   # % de pixels avec NDVI > 0.3
        'density_class': str,    # 'sparse' | 'moderate' | 'dense' | 'very_dense'
        'image_date':   str,     # date de l'image utilisée
      }
    """
    init_gee()
    zone = bbox_to_ee_geometry(lon_min, lat_min, lon_max, lat_max)

    # Collection Sentinel-2 Surface Reflectance
    collection = (
        ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(zone)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
        .sort('CLOUDY_PIXEL_PERCENTAGE')
    )

    # Image médiane pour réduire l'effet des nuages
    image = collection.median()

    # Calcul NDVI : (NIR - Rouge) / (NIR + Rouge)
    # Sentinel-2 : B8 = NIR, B4 = Rouge
    ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')

    # Statistiques
    stats = ndvi.reduceRegion(
        reducer   = ee.Reducer.mean().combine(ee.Reducer.max(), sharedInputs=True),
        geometry  = zone,
        scale     = 30,
        maxPixels = 1e9,
    ).getInfo()

    ndvi_mean = round(stats.get('NDVI_mean', 0) or 0, 3)
    ndvi_max  = round(stats.get('NDVI_max',  0) or 0, 3)

    # Pourcentage de végétation (pixels avec NDVI > 0.3)
    veg_mask   = ndvi.gt(0.3)
    veg_stats  = veg_mask.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=zone, scale=30, maxPixels=1e9
    ).getInfo()
    coverage = round((veg_stats.get('NDVI', 0) or 0) * 100, 1)

    # Date de l'image la plus récente utilisée
    try:
        latest = collection.limit(1, 'CLOUDY_PIXEL_PERCENTAGE').first()
        img_date = ee.Date(latest.get('system:time_start')).format('YYYY-MM-dd').getInfo()
    except Exception:
        img_date = date_end

    return {
        'ndvi_mean':     ndvi_mean,
        'ndvi_max':      ndvi_max,
        'coverage_pct':  coverage,
        'density_class': _ndvi_to_class(ndvi_mean),
        'image_date':    img_date,
    }


def _ndvi_to_class(ndvi):
    if ndvi < 0.15: return 'sparse'
    if ndvi < 0.35: return 'moderate'
    if ndvi < 0.55: return 'dense'
    return 'very_dense'


# ── Analyse Inondation — SAR Sentinel-1 ───────────────────

def get_flood_risk_stats(lon_min, lat_min, lon_max, lat_max,
                         date_start='2025-10-01', date_end='2025-12-31'):
    """
    Estime le risque d'inondation via SAR Sentinel-1.

    Le radar à synthèse d'ouverture détecte les surfaces d'eau
    (valeurs VV très basses = forte réflexion spéculaire = eau).

    Retourne un dict :
      {
        'risk_score':  int,    # 0-100
        'risk_level':  str,    # 'faible' | 'modere' | 'eleve' | 'critique'
        'vv_mean_db':  float,  # Signal SAR moyen en dB
        'water_pct':   float,  # % de pixels détectés comme eau
        'rainfall_mm': float,  # Précipitations estimées (CHIRPS)
      }
    """
    init_gee()
    zone = bbox_to_ee_geometry(lon_min, lat_min, lon_max, lat_max)

    # Sentinel-1 GRD — polarisation VV en mode IW
    sar = (
        ee.ImageCollection('COPERNICUS/S1_GRD')
        .filterBounds(zone)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.eq('instrumentMode', 'IW'))
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
        .select('VV')
        .median()
    )

    stats = sar.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=zone, scale=30, maxPixels=1e9
    ).getInfo()
    vv_mean = stats.get('VV', -15) or -15

    # Détection eau : VV < -18 dB
    water_mask  = sar.lt(-18)
    water_stats = water_mask.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=zone, scale=30, maxPixels=1e9
    ).getInfo()
    water_pct = round((water_stats.get('VV', 0) or 0) * 100, 1)

    # Score de risque : combinaison VV et % eau
    # VV très négatif (-25 dB) = eau = risque max
    # VV normal (-10 dB) = sol sec = risque min
    vv_score    = max(0, min(100, int((-vv_mean - 5) * 4)))
    water_score = min(100, int(water_pct * 3))
    risk_score  = int(vv_score * 0.6 + water_score * 0.4)

    # Précipitations CHIRPS
    try:
        chirps = (
            ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
            .filterBounds(zone)
            .filterDate(date_start, date_end)
            .sum()
            .select('precipitation')
        )
        rain_stats = chirps.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=zone, scale=5000, maxPixels=1e9
        ).getInfo()
        rainfall = round(rain_stats.get('precipitation', 0) or 0, 1)
    except Exception:
        rainfall = 0

    return {
        'risk_score':  risk_score,
        'risk_level':  _score_to_risk(risk_score),
        'vv_mean_db':  round(vv_mean, 2),
        'water_pct':   water_pct,
        'rainfall_mm': rainfall,
    }


def _score_to_risk(score):
    if score < 25: return 'faible'
    if score < 50: return 'modere'
    if score < 75: return 'eleve'
    return 'critique'


# ── Analyse Routes — Détection de dégradation ─────────────

def get_road_condition_score(road_geojson_coords,
                             date_start='2025-06-01', date_end='2025-12-31'):
    """
    Estime l'état d'une route via la détection de changement spectral.

    Utilise Sentinel-2 pour analyser les changements de réflectance
    autour du tracé de la route (proxy de l'état de surface).

    road_geojson_coords : liste de [lng, lat] du tracé GeoJSON LineString
    Retourne un dict :
      {
        'condition_score': float,  # 0-100
        'status':          str,    # 'bon' | 'degrade' | 'critique' | 'ferme'
        'change_index':    float,  # indice de changement spectral
      }
    """
    init_gee()

    # Buffer autour de la route (30m de chaque côté)
    line = ee.Geometry.LineString(road_geojson_coords)
    zone = line.buffer(30)

    # Images Sentinel-2 avant/après pour détecter les changements
    mid_date = '2025-09-01'

    def get_median_image(d_start, d_end):
        return (
            ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            .filterBounds(zone)
            .filterDate(d_start, d_end)
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
            .median()
            .select(['B4', 'B8', 'B11'])  # Rouge, NIR, SWIR
        )

    img_before = get_median_image(date_start, mid_date)
    img_after  = get_median_image(mid_date, date_end)

    # Indice de changement spectral (différence normalisée SWIR)
    change = img_after.select('B11').subtract(img_before.select('B11')) \
                      .abs().rename('change')

    stats = change.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=zone, scale=20, maxPixels=1e9
    ).getInfo()
    change_index = stats.get('change', 0) or 0

    # Convertir le changement en score de condition
    # Changement élevé = dégradation ou perturbation
    condition_score = max(0, min(100, int(100 - change_index * 0.5)))

    return {
        'condition_score': condition_score,
        'status':          _score_to_status(condition_score),
        'change_index':    round(change_index, 2),
    }


def _score_to_status(score):
    if score >= 70: return 'bon'
    if score >= 45: return 'degrade'
    if score >= 20: return 'critique'
    return 'ferme'
