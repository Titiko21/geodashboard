"""
traffic_estimator.py — GéoDash
Estimation du trafic routier par zone.

Combine deux sources :
  1. OSM (déjà en base) : type de route, nombre de voies, vitesse max
     → Score de capacité routière
  2. GEE VIIRS (luminosité nocturne) : proxy de densité urbaine / activité
     → Score d'activité nocturne (corrélé au trafic)

Le score final est une combinaison pondérée des deux.

Place ce fichier dans : dashboard/traffic_estimator.py
"""
import logging
import math

logger = logging.getLogger("dashboard")


# ─── Poids par type de route (proxy de volume de trafic) ─────────────────────

HIGHWAY_TRAFFIC_WEIGHT = {
    "motorway":     100,
    "trunk":         90,
    "primary":       75,
    "secondary":     55,
    "tertiary":      40,
    "residential":   25,
    "service":       15,
    "unclassified":  10,
    "track":          3,
    "path":           1,
    "footway":        1,
}

LANES_MULTIPLIER = {
    1: 0.6,
    2: 1.0,
    3: 1.3,
    4: 1.8,
    5: 2.2,
    6: 2.5,
}

TRAFFIC_LEVELS = [
    (80, "tres_eleve",  "Très élevé",  "#dc2626"),
    (60, "eleve",       "Élevé",       "#f97316"),
    (40, "modere",      "Modéré",      "#eab308"),
    (20, "faible",      "Faible",      "#22c55e"),
    (0,  "tres_faible", "Très faible", "#94a3b8"),
]


def _parse_lanes(notes: str) -> int:
    if not notes:
        return 0
    for part in notes.split("|"):
        part = part.strip()
        if part.startswith("Voies :"):
            try:
                return int(part.replace("Voies :", "").strip())
            except (ValueError, TypeError):
                pass
    return 0


def _parse_maxspeed(notes: str) -> int:
    if not notes:
        return 0
    for part in notes.split("|"):
        part = part.strip()
        if part.startswith("Vitesse max :"):
            try:
                val = part.replace("Vitesse max :", "").replace("km/h", "").strip()
                return int(val)
            except (ValueError, TypeError):
                pass
    return 0


def _parse_highway_type(notes: str) -> str:
    if not notes:
        return "unclassified"
    for part in notes.split("|"):
        part = part.strip()
        if part.startswith("Type OSM :"):
            return part.replace("Type OSM :", "").strip()
    return "unclassified"


def _road_traffic_score(road) -> dict:
    notes = road.notes or ""
    highway = _parse_highway_type(notes)
    lanes = _parse_lanes(notes)
    maxspeed = _parse_maxspeed(notes)

    base = HIGHWAY_TRAFFIC_WEIGHT.get(highway, 10)

    if lanes > 0:
        mult = LANES_MULTIPLIER.get(lanes, min(lanes * 0.4, 3.0))
        base = base * mult

    if maxspeed > 0:
        if maxspeed >= 110:
            base *= 1.4
        elif maxspeed >= 80:
            base *= 1.2
        elif maxspeed >= 50:
            base *= 1.0
        else:
            base *= 0.8

    score = min(round(base), 100)

    return {
        "score": score,
        "highway": highway,
        "lanes": lanes,
        "maxspeed": maxspeed,
    }


def estimate_zone_traffic(zone) -> dict:
    from .models import RoadSegment

    roads = RoadSegment.objects.filter(zone=zone)
    total = roads.count()

    if total == 0:
        return {
            "zone_code": zone.code,
            "zone_name": zone.name,
            "traffic_score": 0,
            "traffic_level": "tres_faible",
            "traffic_label": "Très faible",
            "traffic_color": "#94a3b8",
            "total_roads": 0,
            "road_breakdown": {},
            "capacity_index": 0,
            "top_roads": [],
            "viirs_score": None,
        }

    scores = []
    breakdown = {}
    top_roads = []

    for road in roads:
        result = _road_traffic_score(road)
        sc = result["score"]
        hw = result["highway"]
        scores.append(sc)

        if hw not in breakdown:
            breakdown[hw] = {"count": 0, "total_score": 0}
        breakdown[hw]["count"] += 1
        breakdown[hw]["total_score"] += sc

        if sc >= 60:
            top_roads.append({
                "id": road.id,
                "name": road.name,
                "score": sc,
                "highway": hw,
                "lanes": result["lanes"],
                "maxspeed": result["maxspeed"],
            })

    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    road_breakdown = {}
    for hw, data in breakdown.items():
        road_breakdown[hw] = {
            "count": data["count"],
            "avg_score": round(data["total_score"] / data["count"], 1),
        }

    major_roads = sum(1 for s in scores if s >= 50)
    capacity_index = round((major_roads / max(total, 1)) * 100, 1)

    traffic_level = "tres_faible"
    traffic_label = "Très faible"
    traffic_color = "#94a3b8"
    for threshold, level, label, color in TRAFFIC_LEVELS:
        if avg_score >= threshold:
            traffic_level = level
            traffic_label = label
            traffic_color = color
            break

    top_roads.sort(key=lambda x: x["score"], reverse=True)
    top_roads = top_roads[:10]

    viirs_score = _get_viirs_score(zone)

    if viirs_score is not None:
        final_score = round(avg_score * 0.7 + viirs_score * 0.3, 1)
    else:
        final_score = avg_score

    for threshold, level, label, color in TRAFFIC_LEVELS:
        if final_score >= threshold:
            traffic_level = level
            traffic_label = label
            traffic_color = color
            break

    return {
        "zone_code": zone.code,
        "zone_name": zone.name,
        "traffic_score": final_score,
        "traffic_level": traffic_level,
        "traffic_label": traffic_label,
        "traffic_color": traffic_color,
        "total_roads": total,
        "road_breakdown": road_breakdown,
        "capacity_index": capacity_index,
        "top_roads": top_roads,
        "viirs_score": viirs_score,
    }


def _get_viirs_score(zone) -> float | None:
    try:
        from .gee_integration import get_ee
        ee = get_ee()
        if ee is None:
            return None

        viirs = (ee.ImageCollection("NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG")
                 .sort("system:time_start", False)
                 .first()
                 .select("avg_rad"))

        delta = 0.05
        roi = ee.Geometry.Rectangle([
            zone.lng_center - delta,
            zone.lat_center - delta,
            zone.lng_center + delta,
            zone.lat_center + delta,
        ])

        stats = viirs.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=roi,
            scale=500,
            maxPixels=1e6,
        ).getInfo()

        avg_rad = stats.get("avg_rad")
        if avg_rad is None:
            return None

        normalized = min(round((avg_rad / 40.0) * 100, 1), 100)
        logger.info("VIIRS score zone %s: avg_rad=%.2f -> score=%.1f",
                     zone.code, avg_rad, normalized)
        return max(normalized, 0)

    except Exception as e:
        logger.warning("VIIRS indisponible pour zone %s: %s", zone.code, e)
        return None