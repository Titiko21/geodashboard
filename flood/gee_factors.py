"""
Récupération des facteurs physiographiques via Google Earth Engine.

v2 — fractions de territoire plutôt que moyennes (les moyennes diluaient
les bas-fonds urbanisés dans les grandes communes de plateau) :

  - Copernicus GLO-30 DEM → altitude moyenne/min, % quasi plat (< 2°)
  - MERIT Hydro `hnd`     → HAND moyen + % en zone basse (HAND < 5 m)
  - Dynamic World         → % bâti, % eau, et % DU BÂTI en zone basse
                            (exposition : quartiers construits en bas-fond)

NOTE résolution : 30-90 m = susceptibilité de commune/quartier. L'analyse
« cote de rue » exigera un MNT fin (≤ 12 m), phase ultérieure.
"""
import logging
from datetime import datetime, timedelta

from dashboard.gee_integration import (
    gee_cached,
    get_ee,
    get_land_use_breakdown,
)

logger = logging.getLogger("geodash.flood")

PHYSIO_CACHE_TTL = 60 * 60 * 24 * 7    # 7 jours — physiographie stable

HAND_LOW_M   = 5.0    # seuil « zone basse » (m au-dessus du drainage)
FLAT_DEG     = 2.0    # seuil « quasi plat » (degrés)
DW_WINDOW_D  = 180    # fenêtre Dynamic World (mode 6 mois)
DW_BUILT     = 6      # classe « built » de Dynamic World


@gee_cached("flood_physio_v2", ttl=PHYSIO_CACHE_TTL)
def get_terrain_factors(geom_geojson):
    """
    Facteurs terrain pour une géométrie (dict GeoJSON).

    Renvoie {"elevation_mean_m", "elevation_min_m", "slope_mean_deg",
    "hand_mean_m", "hand_low_pct", "flat_pct", "built_low_pct"}
    (valeurs None si indisponibles), ou None si GEE inaccessible.
    """
    ee = get_ee()
    if ee is None:
        logger.error("[Flood] GEE indisponible — facteurs terrain non calculés")
        return None
    if not geom_geojson:
        return None

    try:
        region = ee.Geometry(geom_geojson)

        # mosaic() perd la projection native → fixée explicitement, sinon
        # ee.Terrain.slope calcule sur une projection à 1° (aberrant).
        dem = (
            ee.ImageCollection("COPERNICUS/DEM/GLO30")
            .select("DEM").mosaic()
            .setDefaultProjection("EPSG:4326", None, 30)
        )
        slope = ee.Terrain.slope(dem)
        hand = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")

        hand_low = hand.lt(HAND_LOW_M)     # masque 0/1 « zone basse »
        flat = slope.lt(FLAT_DEG)          # masque 0/1 « quasi plat »

        elev_stats = dem.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
            geometry=region, scale=30, maxPixels=1e9,
        ).getInfo()
        slope_stats = slope.reduceRegion(
            ee.Reducer.mean(), region, scale=30, maxPixels=1e9,
        ).getInfo()
        hand_stats = hand.addBands(hand_low.rename("low")).reduceRegion(
            ee.Reducer.mean(), region, scale=90, maxPixels=1e9,
        ).getInfo()
        flat_stats = flat.reduceRegion(
            ee.Reducer.mean(), region, scale=30, maxPixels=1e9,
        ).getInfo()

        # Exposition : part du BÂTI (Dynamic World, mode 6 mois) située en
        # zone basse. C'est le facteur « quartiers construits en bas-fond ».
        built_low_pct = None
        end = datetime.utcnow()
        start = end - timedelta(days=DW_WINDOW_D)
        dw = (
            ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
            .filterBounds(region)
            .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            .select("label")
        )
        if dw.size().getInfo() > 0:
            built = dw.reduce(ee.Reducer.mode()).eq(DW_BUILT)
            sums = (
                built.rename("built")
                .addBands(built.And(hand_low).rename("built_low"))
                .reduceRegion(ee.Reducer.sum(), region, scale=30, maxPixels=1e9)
                .getInfo()
            )
            b = sums.get("built") or 0
            bl = sums.get("built_low") or 0
            if b > 0:
                built_low_pct = round(bl / b * 100.0, 1)

        def _r(v, nd=1):
            return round(v, nd) if v is not None else None

        def _pct(v):
            return round(v * 100.0, 1) if v is not None else None

        return {
            "elevation_mean_m": _r(elev_stats.get("DEM_mean")),
            "elevation_min_m":  _r(elev_stats.get("DEM_min")),
            "slope_mean_deg":   _r(slope_stats.get("slope"), 2),
            "hand_mean_m":      _r(hand_stats.get("hnd"), 2),
            "hand_low_pct":     _pct(hand_stats.get("low")),
            "flat_pct":         _pct(flat_stats.get("slope")),
            "built_low_pct":    built_low_pct,
        }
    except Exception as exc:
        logger.error("[Flood] Échec facteurs terrain : %s", exc, exc_info=True)
        return None


def get_physio_factors(geom_geojson):
    """
    Facteurs physiographiques complets : terrain + occupation du sol.
    Renvoie un dict prêt pour flood.scoring.compute() (clés manquantes =
    None), ou None si rien n'a pu être calculé.
    """
    terrain = get_terrain_factors(geom_geojson) or {}
    landuse = get_land_use_breakdown(geom_geojson) or {}

    if not terrain and not landuse:
        return None

    return {
        "elevation_mean_m": terrain.get("elevation_mean_m"),
        "elevation_min_m":  terrain.get("elevation_min_m"),
        "slope_mean_deg":   terrain.get("slope_mean_deg"),
        "hand_mean_m":      terrain.get("hand_mean_m"),
        "hand_low_pct":     terrain.get("hand_low_pct"),
        "flat_pct":         terrain.get("flat_pct"),
        "built_low_pct":    terrain.get("built_low_pct"),
        "urban_pct":        landuse.get("urban"),
        "water_pct":        landuse.get("water"),
    }
