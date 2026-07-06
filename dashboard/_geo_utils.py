"""
Helpers géométriques neutres (sans I/O réseau, sans dépendance à une source).

Extraits de l'ancien `populate_geodata` (importeur OSM/Overpass supprimé) pour
rester disponibles au reste de l'app : réparation d'alertes, futur importeur
générique multi-format (Phase A), tests.
"""
import json as _json
import math


def geojson_to_geom(geojson_value):
    """
    Convertit un dict GeoJSON en GEOSGeometry SRID 4326, ou None si invalide.
    Utilisé pour peupler le champ `geom` PostGIS en parallèle du `geojson` JSON.

    Règles :
      - LineString : >= 2 points
      - Polygon : chaque ring >= 4 points (auto-fermé si nécessaire)
      - Skip silencieux pour tout ce qui ne parse pas / est vide.
    """
    from django.contrib.gis.geos import GEOSGeometry

    if not geojson_value or not isinstance(geojson_value, dict):
        return None
    gtype  = geojson_value.get("type")
    coords = geojson_value.get("coordinates")
    if not gtype or coords is None:
        return None

    if gtype == "LineString":
        if not isinstance(coords, list) or len(coords) < 2:
            return None
    elif gtype == "Polygon":
        if not isinstance(coords, list) or not coords:
            return None
        for ring in coords:
            if not isinstance(ring, list):
                return None
            if ring and ring[0] != ring[-1]:
                ring.append(ring[0])
            if len(ring) < 4:
                return None
    # Autres types (MultiPolygon, etc.) : on tente le parse direct.

    try:
        g = GEOSGeometry(_json.dumps(geojson_value), srid=4326)
        return None if g.empty else g
    except Exception:
        return None


def geojson_centroid(geo):
    """Centroïde approximé (lat, lng) à partir d'un GeoJSON.

    Moyenne arithmétique des sommets — suffisant pour positionner une alerte
    sur la carte. Supporte Point, LineString, MultiLineString, Polygon et
    MultiPolygon. Retourne (None, None) si le GeoJSON est vide ou inattendu.
    """
    if not isinstance(geo, dict):
        return (None, None)
    gtype  = geo.get("type")
    coords = geo.get("coordinates")
    if not coords:
        return (None, None)

    pts = []
    if gtype == "Point":
        pts = [coords]
    elif gtype == "LineString":
        pts = coords
    elif gtype == "MultiLineString":
        for line in coords:
            pts.extend(line)
    elif gtype == "Polygon":
        if coords and isinstance(coords[0], list):
            pts = coords[0]
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly and isinstance(poly[0], list):
                pts.extend(poly[0])
    else:
        return (None, None)

    valid = [p for p in pts
             if isinstance(p, (list, tuple)) and len(p) >= 2
             and isinstance(p[0], (int, float))
             and isinstance(p[1], (int, float))]
    if not valid:
        return (None, None)

    avg_lng = sum(p[0] for p in valid) / len(valid)
    avg_lat = sum(p[1] for p in valid) / len(valid)
    return (avg_lat, avg_lng)


def polygon_area_km2(geometry: list) -> float:
    """Surface approx. (km²) d'un anneau [{lat, lon}, ...] via shoelace projeté."""
    if len(geometry) < 3:
        return 0.0
    lat0 = sum(p["lat"] for p in geometry) / len(geometry)
    cos_lat = math.cos(math.radians(lat0))
    R = 6_371_000.0
    pts = [
        (math.radians(p["lon"]) * R * cos_lat,
         math.radians(p["lat"]) * R)
        for p in geometry
    ]
    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return round(abs(area) / 2.0 / 1_000_000, 4)
