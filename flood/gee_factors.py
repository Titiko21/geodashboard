"""
Récupération des facteurs physiographiques via Google Earth Engine.

Sources (toutes gratuites, résolution 30-90 m) :
  - Copernicus GLO-30 DEM → altitude moyenne/min, pente moyenne
  - MERIT Hydro `hnd`     → HAND moyen (hauteur au-dessus du drainage)
  - Dynamic World         → % bâti, % eau (via get_land_use_breakdown)

NOTE résolution : suffisant pour la susceptibilité de commune/quartier.
L'analyse « cote de rue » (rue plus basse que son voisinage) exigera un
MNT fin (≤ 12 m) — ArcGIS/levé national, phase ultérieure.
"""
import logging

from dashboard.gee_integration import (
    gee_cached,
    get_ee,
    get_land_use_breakdown,
)

logger = logging.getLogger("geodash.flood")

# Cache long : la physiographie ne change pas à l'échelle de la semaine.
PHYSIO_CACHE_TTL = 60 * 60 * 24 * 7


@gee_cached("flood_physio_v1", ttl=PHYSIO_CACHE_TTL)
def get_terrain_factors(geom_geojson):
    """
    Facteurs terrain pour une géométrie (dict GeoJSON).

    Renvoie {"elevation_mean_m", "elevation_min_m", "slope_mean_deg",
    "hand_mean_m"} (valeurs None si indisponibles), ou None si GEE
    est inaccessible / géométrie invalide.
    """
    ee = get_ee()
    if ee is None:
        logger.error("[Flood] GEE indisponible — facteurs terrain non calculés")
        return None
    if not geom_geojson:
        return None

    try:
        region = ee.Geometry(geom_geojson)

        # Le mosaic() perd la projection native → on la fixe explicitement,
        # sinon ee.Terrain.slope calcule sur une projection à 1° (résultats
        # aberrants).
        dem = (
            ee.ImageCollection("COPERNICUS/DEM/GLO30")
            .select("DEM")
            .mosaic()
            .setDefaultProjection("EPSG:4326", None, 30)
        )
        slope = ee.Terrain.slope(dem)
        hand = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")

        elev_stats = dem.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True),
            geometry=region, scale=30, maxPixels=1e9,
        ).getInfo()
        slope_stats = slope.reduceRegion(
            ee.Reducer.mean(), region, scale=30, maxPixels=1e9,
        ).getInfo()
        hand_stats = hand.reduceRegion(
            ee.Reducer.mean(), region, scale=90, maxPixels=1e9,
        ).getInfo()

        def _r(v, nd=1):
            return round(v, nd) if v is not None else None

        return {
            "elevation_mean_m": _r(elev_stats.get("DEM_mean")),
            "elevation_min_m":  _r(elev_stats.get("DEM_min")),
            "slope_mean_deg":   _r(slope_stats.get("slope"), 2),
            "hand_mean_m":      _r(hand_stats.get("hnd"), 2),
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
        "urban_pct":        landuse.get("urban"),
        "water_pct":        landuse.get("water"),
    }
