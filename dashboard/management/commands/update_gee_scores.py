"""
update_gee_scores.py — GéoDash (Production)

Remplace les scores simulés (basés sur osm_id % N) par des mesures
satellite réelles via Google Earth Engine, en sampling per-géométrie.

Architecture :
  ┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
  │ Sentinel-2 SR   │ ──→ │ reduceRegions()  │ ──→ │ NDVI réel         │
  │ Harmonized      │     │ per polygone vég │     │ per polygone      │
  └─────────────────┘     └──────────────────┘     └───────────────────┘
  ┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
  │ Sentinel-1 GRD  │ ──→ │ reduceRegions()  │ ──→ │ Risque inondation │
  │ VV SAR          │     │ per polygone eau │     │ per polygone      │
  └─────────────────┘     └──────────────────┘     └───────────────────┘
  ┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
  │ Landsat 8 SR    │ ──→ │ reduceRegions()  │ ──→ │ Surface routière  │
  │ (optionnel)     │     │ per segment route│     │ OSM 70% + GEE 30% │
  └─────────────────┘     └──────────────────┘     └───────────────────┘

Pourquoi reduceRegions() :
  - Un seul appel GEE par type de donnée et par zone (pas N appels)
  - Résultat exact par géométrie (pas de distribution artificielle)
  - Respecte les quotas GEE (batch ≤ 200 features)

Usage :
    python manage.py update_gee_scores                  # toutes les zones
    python manage.py update_gee_scores --zone DAL       # une seule zone
    python manage.py update_gee_scores --dry-run        # simulation
    python manage.py update_gee_scores --skip-roads     # sans routes
"""

import logging
import re
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from dashboard.models import (
    Alert,
    FloodRisk,
    RoadSegment,
    VegetationDensity,
    Zone,
)

logger = logging.getLogger("geodash.gee")


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════════════════════════════════

# ── Sentinel-2 SR Harmonized — NDVI végétation ──
S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
S2_SCALE = 10           # résolution native (m)
S2_CLOUD_MAX = 20       # % nuages max
S2_WINDOWS_DAYS = [30, 60, 90]  # fenêtres de recherche (escalade)

# ── Sentinel-1 GRD — SAR détection inondation ──
S1_COLLECTION = "COPERNICUS/S1_GRD"
S1_SCALE = 10
S1_RECENT_DAYS = 14     # période "après"
S1_FLOOD_THRESHOLD_DB = -3.0  # seuil de détection (dB)

# ── Landsat 8 Collection 2 — NDWI surface routière (expérimental) ──
L8_COLLECTION = "LANDSAT/LC08/C02/T1_L2"
L8_SCALE = 30
L8_CLOUD_MAX = 30
L8_DAYS = 60

# ── Processing ──
REDUCE_BATCH = 200      # max features par appel reduceRegions
BBOX_DELTA = 0.06       # ° autour du centroïde (> 0.05 du rayon d'import)
INTER_ZONE_PAUSE = 2.0  # secondes entre zones (quota GEE)

# ── Calibration SAR → score de risque ──
SAR_BASE_RISK = {
    "river": 40, "rivière": 40, "fleuve": 45,
    "canal": 35,
    "stream": 20, "ruisseau": 20, "marigot": 25,
    "wetland": 45, "marécage": 45, "marais": 45,
    "water": 25, "lac": 30, "lagune": 35, "étang": 25,
}
SAR_BASE_DEFAULT = 30
SAR_SENSITIVITY = 8     # points de risque par -1 dB de changement
SAR_FLOOD_BONUS_THRESHOLD = 0.1  # fraction min pour le bonus inondation
SAR_FLOOD_BONUS_MAX = 20         # points max du bonus

# ── Blending score routier ──
ROAD_OSM_WEIGHT = 0.70
ROAD_GEE_WEIGHT = 0.30


# ═══════════════════════════════════════════════════════════════════════════════
# FONCTIONS UTILITAIRES
# ═══════════════════════════════════════════════════════════════════════════════

def _ndvi_to_density(ndvi):
    """Classifie un NDVI en catégorie de densité."""
    if ndvi < 0.2:
        return "sparse"
    if ndvi < 0.4:
        return "moderate"
    if ndvi < 0.6:
        return "dense"
    return "very_dense"


def _risk_to_level(score):
    """Convertit un score de risque en niveau textuel."""
    if score >= 70:
        return "critique"
    if score >= 50:
        return "eleve"
    if score >= 30:
        return "modere"
    return "faible"


def _score_to_status(score):
    """Convertit un score de condition en statut routier."""
    if score >= 70:
        return "bon"
    if score >= 45:
        return "degrade"
    if score >= 20:
        return "critique"
    return "ferme"


def _water_type_from_name(name):
    """
    Identifie le type de corps d'eau depuis le nom de l'objet.
    Cherche dans l'ordre du plus spécifique au plus générique.
    """
    lower = (name or "").lower()
    for keyword in SAR_BASE_RISK:
        if keyword in lower:
            return keyword
    return ""


def _geometry_centroid(geojson):
    """
    Centroïde approximatif d'un GeoJSON.
    Retourne (lat, lng) ou (None, None).
    Suffisant pour le positionnement d'alertes — pas pour du calcul géodésique.
    """
    if not geojson:
        return None, None

    geo_type = geojson.get("type", "")
    coords = geojson.get("coordinates", [])
    if not coords:
        return None, None

    points = []
    if geo_type == "LineString":
        points = coords
    elif geo_type == "Polygon":
        points = coords[0] if coords else []
    elif geo_type == "MultiLineString":
        for line in coords:
            points.extend(line)
    elif geo_type == "MultiPolygon":
        for poly in coords:
            if poly:
                points.extend(poly[0])

    if not points:
        return None, None

    avg_lng = sum(p[0] for p in points) / len(points)
    avg_lat = sum(p[1] for p in points) / len(points)
    return round(avg_lat, 6), round(avg_lng, 6)


def _make_region(ee, zone):
    """
    Construit le rectangle GEE de la zone.
    Couvre la zone d'import Overpass (±0.05°) + marge.
    """
    d = BBOX_DELTA
    return ee.Geometry.Rectangle([
        zone.lng_center - d,
        zone.lat_center - d,
        zone.lng_center + d,
        zone.lat_center + d,
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ACQUISITION D'IMAGES SATELLITE
# ═══════════════════════════════════════════════════════════════════════════════

def _acquire_s2_ndvi(ee, region):
    """
    Image NDVI Sentinel-2 la plus récente avec couverture nuageuse acceptable.

    Recherche en fenêtres croissantes (30j → 60j → 90j) pour maximiser
    les chances de trouver une image exploitable.

    Retourne (image_2bandes, date_str) ou (None, None).
    Bandes :
      - NDVI : indice de végétation [-1, 1]
      - veg_cover : masque binaire (1 si NDVI > 0.2, 0 sinon)
    """
    now = datetime.utcnow()

    for window in S2_WINDOWS_DAYS:
        start = (now - timedelta(days=window)).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")

        col = (
            ee.ImageCollection(S2_COLLECTION)
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_CLOUD_MAX))
            .sort("system:time_start", False)
        )

        size = col.size().getInfo()
        if size == 0:
            continue

        image = col.first()

        # Vérification explicite des bandes (protection lazy evaluation GEE)
        bands = image.bandNames().getInfo()
        if "B8" not in bands or "B4" not in bands:
            logger.warning("[S2] Bandes B8/B4 manquantes — fenêtre %dj", window)
            continue

        ndvi = (
            image.select(["B8", "B4"])
            .normalizedDifference(["B8", "B4"])
            .rename("NDVI")
        )
        veg_mask = ndvi.gt(0.2).rename("veg_cover")
        combined = ndvi.addBands(veg_mask)

        ts = image.get("system:time_start").getInfo()
        image_date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")

        logger.info(
            "[S2] OK : %d images, fenêtre %dj, date=%s",
            size, window, image_date,
        )
        return combined, image_date

    return None, None


def _acquire_s1_sar_diff(ee, region):
    """
    Différence SAR Sentinel-1 (after - before) pour la détection d'inondation.

    Principe : la rétrodiffusion radar baisse sur les surfaces inondées
    (réflexion spéculaire). Une baisse > 3 dB indique une inondation probable.

    Retourne (image_2bandes, True) ou (None, None).
    Bandes :
      - VV : changement de rétrodiffusion (dB, négatif = plus humide)
      - flooded : masque binaire (1 si changement < seuil)
    """
    now = datetime.utcnow()
    after_start = (now - timedelta(days=S1_RECENT_DAYS)).strftime("%Y-%m-%d")
    after_end = now.strftime("%Y-%m-%d")
    before_start = (now - timedelta(days=S1_RECENT_DAYS * 3)).strftime("%Y-%m-%d")
    before_end = after_start

    def _s1_col(start, end):
        return (
            ee.ImageCollection(S1_COLLECTION)
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(
                ee.Filter.listContains("transmitterReceiverPolarisation", "VV")
            )
            .select("VV")
        )

    col_before = _s1_col(before_start, before_end)
    col_after = _s1_col(after_start, after_end)

    n_before = col_before.size().getInfo()
    n_after = col_after.size().getInfo()

    if n_before == 0 or n_after == 0:
        logger.info(
            "[S1] Données insuffisantes (before=%d, after=%d)", n_before, n_after,
        )
        return None, None

    before = col_before.mean()
    after = col_after.mean()

    diff = after.subtract(before).rename("VV")
    flooded = diff.lt(S1_FLOOD_THRESHOLD_DB).rename("flooded")
    combined = diff.addBands(flooded)

    logger.info("[S1] OK : before=%d, after=%d images", n_before, n_after)
    return combined, True


def _acquire_l8_ndwi(ee, region):
    """
    NDWI médian Landsat 8 pour l'évaluation de surface routière.

    NDWI élevé → surface humide/dégradée → score bas
    NDWI faible → surface sèche/intact → score haut

    Retourne l'image NDWI (bande 'nd') ou None.
    """
    now = datetime.utcnow()
    start = (now - timedelta(days=L8_DAYS)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    col = (
        ee.ImageCollection(L8_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUD_COVER", L8_CLOUD_MAX))
    )

    size = col.size().getInfo()
    if size == 0:
        return None

    img = col.median().select(["SR_B3", "SR_B5"])

    bands = img.bandNames().getInfo()
    if "SR_B3" not in bands or "SR_B5" not in bands:
        logger.warning("[L8] Bandes SR_B3/SR_B5 manquantes")
        return None

    # Scale factor Landsat Collection 2
    img = img.multiply(0.0000275).add(-0.2)
    ndwi = img.normalizedDifference(["SR_B3", "SR_B5"])

    logger.info("[L8] OK : %d images", size)
    return ndwi


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSION GÉOMÉTRIES & reduceRegions
# ═══════════════════════════════════════════════════════════════════════════════

def _to_ee_feature(ee, geojson, db_id):
    """
    Convertit un GeoJSON Django en ee.Feature avec db_id en propriété.
    Retourne None si la géométrie est invalide ou vide.
    """
    if not geojson or not geojson.get("type") or not geojson.get("coordinates"):
        return None
    try:
        geom = ee.Geometry(geojson)
        return ee.Feature(geom, {"db_id": db_id})
    except Exception as exc:
        logger.debug("Géométrie invalide (id=%d) : %s", db_id, exc)
        return None


def _reduce_regions(ee, image, features, scale):
    """
    Exécute image.reduceRegions(mean) en batchs de REDUCE_BATCH features.

    Retourne {db_id: {band_name: value, ...}} pour chaque feature
    où le reducer a retourné une valeur non-null.

    Le batching évite les timeouts GEE sur les zones avec beaucoup d'objets.
    """
    if not features:
        return {}

    results = {}

    for start_idx in range(0, len(features), REDUCE_BATCH):
        batch = features[start_idx : start_idx + REDUCE_BATCH]
        fc = ee.FeatureCollection(batch)

        try:
            reduced = image.reduceRegions(
                collection=fc,
                reducer=ee.Reducer.mean(),
                scale=scale,
            ).getInfo()
        except Exception as exc:
            logger.warning(
                "reduceRegions batch [%d:%d] échoué : %s",
                start_idx,
                start_idx + len(batch),
                exc,
            )
            continue

        for feat in reduced.get("features", []):
            props = feat.get("properties", {})
            db_id = props.pop("db_id", None)
            if db_id is not None:
                results[db_id] = props

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# MISE À JOUR PAR TYPE DE DONNÉES
# ═══════════════════════════════════════════════════════════════════════════════

def _update_vegetation(zone, ee, region, dry_run, stdout):
    """
    Met à jour chaque polygone végétation avec son NDVI réel mesuré par satellite.

    Pour chaque polygone :
      - NDVI = moyenne des pixels Sentinel-2 intersectant le polygone
      - veg_cover = fraction des pixels avec NDVI > 0.2
      - change_vs_previous = delta avec la valeur précédente

    Retourne (nb_mis_à_jour, date_image).
    """
    veg_qs = list(VegetationDensity.objects.filter(zone=zone))
    if not veg_qs:
        return 0, ""

    # 1. Acquisition image
    ndvi_image, image_date = _acquire_s2_ndvi(ee, region)
    if ndvi_image is None:
        stdout.write("    NDVI : aucune image Sentinel-2 disponible")
        return 0, ""

    # 2. Construction des features GEE
    features = []
    obj_map = {}
    for v in veg_qs:
        feat = _to_ee_feature(ee, v.geojson, v.id)
        if feat is not None:
            features.append(feat)
            obj_map[v.id] = v

    if not features:
        stdout.write("    NDVI : aucune géométrie valide")
        return 0, image_date

    # 3. Sampling per-polygone
    results = _reduce_regions(ee, ndvi_image, features, S2_SCALE)

    # 4. Mise à jour des objets Django
    now = timezone.now()
    to_update = []

    for db_id, props in results.items():
        mean_ndvi = props.get("NDVI")
        coverage_frac = props.get("veg_cover")

        if mean_ndvi is None:
            continue

        obj = obj_map.get(db_id)
        if obj is None:
            continue

        new_ndvi = round(max(-1.0, min(1.0, mean_ndvi)), 4)
        old_ndvi = obj.ndvi_value or 0.0

        obj.change_vs_previous = round(new_ndvi - old_ndvi, 4)
        obj.ndvi_value = new_ndvi
        obj.density_class = _ndvi_to_density(new_ndvi)
        obj.coverage_percent = (
            round(coverage_frac * 100, 1)
            if coverage_frac is not None
            else round(max(0.0, new_ndvi) * 100, 1)
        )
        obj.last_analyzed = now
        to_update.append(obj)

    if not dry_run and to_update:
        VegetationDensity.objects.bulk_update(
            to_update,
            [
                "ndvi_value",
                "density_class",
                "coverage_percent",
                "change_vs_previous",
                "last_analyzed",
            ],
            batch_size=200,
        )

    sampled = len(to_update)
    no_pixel = len(features) - sampled
    stdout.write(
        f"    NDVI : {sampled}/{len(veg_qs)} polygones "
        f"(image {image_date}"
        + (f", {no_pixel} sans pixels" if no_pixel else "")
        + ")"
    )
    return sampled, image_date


def _update_floods(zone, ee, region, dry_run, stdout):
    """
    Met à jour chaque polygone inondation avec le risque SAR réel.

    Pour chaque polygone :
      - mean_change = changement moyen de rétrodiffusion VV (dB)
      - flooded_frac = fraction de pixels détectés comme inondés
      - risk_score = base_type + composante_SAR + bonus_inondation

    Le score de base vient du type de corps d'eau (river > stream).
    La composante SAR mesure l'intensité du changement.
    Le bonus inondation récompense les zones avec beaucoup de pixels inondés.

    Retourne le nombre de polygones mis à jour.
    """
    flood_qs = list(FloodRisk.objects.filter(zone=zone))
    if not flood_qs:
        return 0

    # 1. Acquisition SAR
    sar_combined, available = _acquire_s1_sar_diff(ee, region)
    if sar_combined is None:
        stdout.write("    SAR : données Sentinel-1 insuffisantes")
        return 0

    # 2. Construction des features GEE
    features = []
    obj_map = {}
    for f in flood_qs:
        feat = _to_ee_feature(ee, f.geojson, f.id)
        if feat is not None:
            features.append(feat)
            obj_map[f.id] = f

    if not features:
        stdout.write("    SAR : aucune géométrie valide")
        return 0

    # 3. Sampling per-polygone
    results = _reduce_regions(ee, sar_combined, features, S1_SCALE)

    # 4. Calcul du score de risque et mise à jour
    now = timezone.now()
    to_update = []

    for db_id, props in results.items():
        mean_change = props.get("VV")
        flooded_frac = props.get("flooded")

        if mean_change is None:
            continue

        obj = obj_map.get(db_id)
        if obj is None:
            continue

        # Score de base selon le type de corps d'eau
        wtype = _water_type_from_name(obj.name)
        base = SAR_BASE_RISK.get(wtype, SAR_BASE_DEFAULT)

        # Composante SAR : négatif = plus humide = plus de risque
        sar_component = max(0.0, -mean_change * SAR_SENSITIVITY)

        # Bonus si une fraction significative de pixels est inondée
        flood_bonus = 0.0
        if (
            flooded_frac is not None
            and flooded_frac > SAR_FLOOD_BONUS_THRESHOLD
        ):
            flood_bonus = min(
                flooded_frac * SAR_FLOOD_BONUS_MAX / 0.5,
                SAR_FLOOD_BONUS_MAX,
            )

        risk_score = round(
            min(100.0, max(0.0, base + sar_component + flood_bonus)), 1
        )

        obj.risk_score = risk_score
        obj.risk_level = _risk_to_level(risk_score)
        obj.last_analyzed = now
        to_update.append(obj)

    if not dry_run and to_update:
        FloodRisk.objects.bulk_update(
            to_update,
            ["risk_score", "risk_level", "last_analyzed"],
            batch_size=200,
        )

    sampled = len(to_update)
    no_pixel = len(features) - sampled
    stdout.write(
        f"    SAR : {sampled}/{len(flood_qs)} polygones"
        + (f" ({no_pixel} sans pixels)" if no_pixel else "")
    )
    return sampled


def _update_roads(zone, ee, region, dry_run, stdout):
    """
    Ajuste les scores routiers en combinant le score OSM existant (70%)
    avec un indice de surface dérivé du NDWI Landsat (30%).

    Le NDWI (Normalized Difference Water Index) est un proxy de l'humidité
    de surface. Une route en bon état (bitume sec) a un NDWI bas.
    Une route dégradée / inondée a un NDWI plus élevé.

    Note : cette mesure est expérimentale. Le score OSM (basé sur highway,
    smoothness, surface) reste la source principale.

    Retourne le nombre de segments mis à jour.
    """
    road_qs = list(RoadSegment.objects.filter(zone=zone))
    if not road_qs:
        return 0

    # 1. Acquisition Landsat
    ndwi_image = _acquire_l8_ndwi(ee, region)
    if ndwi_image is None:
        stdout.write("    Routes : pas de Landsat — scores OSM conservés")
        return 0

    # 2. Construction des features GEE (LineStrings)
    features = []
    obj_map = {}
    for r in road_qs:
        feat = _to_ee_feature(ee, r.geojson, r.id)
        if feat is not None:
            features.append(feat)
            obj_map[r.id] = r

    if not features:
        return 0

    # 3. Sampling le long des segments
    results = _reduce_regions(ee, ndwi_image, features, L8_SCALE)

    # 4. Blending OSM + GEE
    now = timezone.now()
    to_update = []

    for db_id, props in results.items():
        mean_ndwi = props.get("nd")
        if mean_ndwi is None:
            continue

        obj = obj_map.get(db_id)
        if obj is None:
            continue

        # Conversion NDWI → indice de surface [0, 100]
        # NDWI bas (< -0.2) → surface sèche/intacte → score élevé
        # NDWI haut (> 0.3) → surface humide/dégradée → score bas
        gee_score = max(0, min(100, int((-mean_ndwi + 0.5) * 100)))

        # Pondération OSM + GEE
        osm_score = obj.condition_score or 50
        blended = round(osm_score * ROAD_OSM_WEIGHT + gee_score * ROAD_GEE_WEIGHT)
        blended = max(5, min(100, blended))

        obj.condition_score = blended
        obj.status = _score_to_status(blended)
        obj.last_analyzed = now

        # Traçabilité : injecter le score GEE dans les notes
        marker = f"GEE: {gee_score}/100"
        if obj.notes and "GEE:" in obj.notes:
            obj.notes = re.sub(r"GEE: \d+/100", marker, obj.notes)
        else:
            obj.notes = ((obj.notes or "") + f" | {marker}").lstrip(" |")

        to_update.append(obj)

    if not dry_run and to_update:
        RoadSegment.objects.bulk_update(
            to_update,
            ["condition_score", "status", "last_analyzed", "notes"],
            batch_size=200,
        )

    sampled = len(to_update)
    stdout.write(
        f"    Routes : {sampled}/{len(road_qs)} segments ajustés "
        f"(OSM {int(ROAD_OSM_WEIGHT * 100)}% + GEE {int(ROAD_GEE_WEIGHT * 100)}%)"
    )
    return sampled


def _fix_alert_coords(zone, dry_run, stdout):
    """
    Remplace les coordonnées des alertes (centroïde zone) par le centroïde
    réel de la géométrie de l'objet concerné.

    Seules les alertes dont les coordonnées changent significativement
    (> 0.0001°, soit ~11m) sont mises à jour.

    Retourne le nombre d'alertes corrigées.
    """
    alerts = list(Alert.objects.filter(zone=zone, is_read=False))
    if not alerts:
        return 0

    # Index rapide des objets par nom pour chaque catégorie
    road_idx = {
        r.name: r
        for r in RoadSegment.objects.filter(zone=zone).only("name", "geojson")
    }
    flood_idx = {
        f.name: f
        for f in FloodRisk.objects.filter(zone=zone).only("name", "geojson")
    }
    veg_idx = {
        v.name: v
        for v in VegetationDensity.objects.filter(zone=zone).only("name", "geojson")
    }

    TITLE_PREFIXES = {
        "road": ("Route dégradée : ", road_idx),
        "flood": ("Risque inondation : ", flood_idx),
        "vegetation": ("Végétation dégradée : ", veg_idx),
    }

    to_update = []

    for alert in alerts:
        cfg = TITLE_PREFIXES.get(alert.category)
        if cfg is None:
            continue

        prefix, idx = cfg
        name = alert.title.replace(prefix, "", 1)
        obj = idx.get(name)

        if obj is None or not getattr(obj, "geojson", None):
            continue

        lat, lng = _geometry_centroid(obj.geojson)
        if lat is None or lng is None:
            continue

        # Ne mettre à jour que si le changement est significatif
        if (
            abs((alert.lat or 0) - lat) < 0.0001
            and abs((alert.lng or 0) - lng) < 0.0001
        ):
            continue

        alert.lat = lat
        alert.lng = lng
        to_update.append(alert)

    if not dry_run and to_update:
        Alert.objects.bulk_update(to_update, ["lat", "lng"], batch_size=100)

    return len(to_update)


# ═══════════════════════════════════════════════════════════════════════════════
# COMMANDE DJANGO
# ═══════════════════════════════════════════════════════════════════════════════

class Command(BaseCommand):
    help = (
        "Met à jour les scores avec des données satellite GEE réelles. "
        "Utilise reduceRegions() pour un sampling per-géométrie."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--zone",
            type=str,
            default=None,
            help="Code zone (ex: DAL). Absent = toutes les zones.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulation sans écriture en base.",
        )
        parser.add_argument(
            "--skip-roads",
            action="store_true",
            help="Ne pas ajuster les scores routiers (désactive le Landsat).",
        )

    def handle(self, *args, **options):
        zone_code = options["zone"]
        dry_run = options["dry_run"]
        skip_roads = options["skip_roads"]

        # ── Initialisation GEE ──────────────────────────────────────────
        from dashboard.gee_integration import get_ee

        ee = get_ee()
        if ee is None:
            self.stderr.write(
                self.style.ERROR(
                    "GEE non disponible. "
                    "Vérifiez GEE_SERVICE_ACCOUNT et GEE_KEY_FILE dans settings.py."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("GEE initialisé."))
        if dry_run:
            self.stdout.write(
                self.style.WARNING("── DRY RUN ── aucune écriture\n")
            )
        if skip_roads:
            self.stdout.write("  Routes : scoring Landsat désactivé\n")

        # ── Sélection des zones ─────────────────────────────────────────
        if zone_code:
            zones = Zone.objects.filter(code__iexact=zone_code)
            if not zones.exists():
                raise CommandError(f"Zone '{zone_code}' introuvable.")
        else:
            zones = Zone.objects.all().order_by("name")

        total = zones.count()
        self.stdout.write(f"Zones à traiter : {total}\n")

        stats = {
            "veg": 0,
            "flood": 0,
            "road": 0,
            "alerts": 0,
            "ok": 0,
            "errors": 0,
        }

        t0 = time.monotonic()

        for i, zone in enumerate(zones, 1):
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"\n{'─' * 50}\n"
                    f"  [{i}/{total}] {zone.name} ({zone.code})"
                )
            )

            region = _make_region(ee, zone)
            zone_ok = True

            # ── Végétation ──────────────────────────────────────────
            try:
                n, _ = _update_vegetation(
                    zone, ee, region, dry_run, self.stdout
                )
                stats["veg"] += n
            except Exception as exc:
                zone_ok = False
                logger.exception("NDVI erreur zone %s", zone.code)
                self.stderr.write(f"    NDVI ERREUR : {exc}")

            # ── Inondation ──────────────────────────────────────────
            try:
                n = _update_floods(
                    zone, ee, region, dry_run, self.stdout
                )
                stats["flood"] += n
            except Exception as exc:
                zone_ok = False
                logger.exception("SAR erreur zone %s", zone.code)
                self.stderr.write(f"    SAR ERREUR : {exc}")

            # ── Routes (optionnel) ──────────────────────────────────
            if not skip_roads:
                try:
                    n = _update_roads(
                        zone, ee, region, dry_run, self.stdout
                    )
                    stats["road"] += n
                except Exception as exc:
                    zone_ok = False
                    logger.exception("Routes erreur zone %s", zone.code)
                    self.stderr.write(f"    Routes ERREUR : {exc}")

            # ── Alertes ─────────────────────────────────────────────
            try:
                n = _fix_alert_coords(zone, dry_run, self.stdout)
                stats["alerts"] += n
                if n:
                    self.stdout.write(
                        f"    Alertes : {n} coordonnées corrigées"
                    )
            except Exception as exc:
                logger.exception("Alertes erreur zone %s", zone.code)

            if zone_ok:
                stats["ok"] += 1
            else:
                stats["errors"] += 1

            # Pause inter-zone (quota GEE)
            if i < total:
                time.sleep(INTER_ZONE_PAUSE)

        # ── Bilan ───────────────────────────────────────────────────────
        elapsed = time.monotonic() - t0
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 50}"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Terminé en {minutes}m{seconds}s"
                + (" (DRY RUN)" if dry_run else "")
            )
        )
        self.stdout.write(
            f"  Zones        : {stats['ok']}/{total} OK"
            + (
                f", {stats['errors']} erreurs"
                if stats["errors"]
                else ""
            )
        )
        self.stdout.write(f"  Végétation   : {stats['veg']} polygones")
        self.stdout.write(f"  Inondation   : {stats['flood']} polygones")
        self.stdout.write(f"  Routes       : {stats['road']} segments")
        self.stdout.write(f"  Alertes      : {stats['alerts']} coordonnées")