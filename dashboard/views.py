"""
GéoDash — views.py
Synchronisé avec models.py :
  RoadSegment / FloodRisk / VegetationDensity / Alert
"""
import json
import logging
from django.shortcuts  import render, get_object_or_404
from django.http       import JsonResponse
from django.views.decorators.http import require_GET
from django.db.models  import Avg
from django.utils      import timezone

from .models import Zone, RoadSegment, FloodRisk, VegetationDensity, Alert

logger = logging.getLogger("geodash")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _js_num(value, default=0):
    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)

def _road_color(score):
    if score is None: return "#94a3b8"
    if score >= 70:   return "#28b857"
    if score >= 40:   return "#e67e22"
    if score >= 10:   return "#f43f5e"
    return "#94a3b8"

def _geojson(obj):
    """Retourne le geojson depuis un JSONField (dict ou string)."""
    g = getattr(obj, 'geojson', None)
    if not g:
        return None
    if isinstance(g, dict):
        return g
    try:
        return json.loads(g)
    except Exception:
        return None

def _zone_bbox(zone):
    """
    Zone n'a pas de bbox → on calcule un bbox approximatif
    de ±0.25° autour du centre.
    """
    if not zone:
        return {"west": -4.25, "south": 5.10, "east": -3.75, "north": 5.60}
    return {
        "west":  zone.lng_center - 0.25,
        "south": zone.lat_center - 0.25,
        "east":  zone.lng_center + 0.25,
        "north": zone.lat_center + 0.25,
    }

def _gee_available():
    try:
        from .gee_integration import _gee_initialized, init_gee
        if not _gee_initialized:
            init_gee()
        return True
    except Exception:
        return False


# ── Vue principale ─────────────────────────────────────────────────────────────

def dashboard(request):
    zone_code = request.GET.get("zone", "")
    zones     = Zone.objects.all().order_by("name")

    # Pas de fallback sur zones.first() : zone_code vide = vraiment toutes les zones.
    # Si le code est invalide, selected_zone reste None et on affiche tout.
    selected_zone = None
    if zone_code:
        selected_zone = Zone.objects.filter(code=zone_code).first()

    # Querysets filtrés ou globaux selon la sélection
    roads_qs  = RoadSegment.objects.filter(zone=selected_zone)       if selected_zone else RoadSegment.objects.all()
    floods_qs = FloodRisk.objects.filter(zone=selected_zone)         if selected_zone else FloodRisk.objects.all()
    veg_qs    = VegetationDensity.objects.filter(zone=selected_zone)  if selected_zone else VegetationDensity.objects.all()

    # Données carte (JSON)
    map_data = {
        "routes": [
            {
                "id":              r.id,
                "name":            r.name,
                "condition_score": r.condition_score,
                "status":          r.status,
                "status_label":    r.get_status_display(),
                "surface_type":    r.surface_type,
                "color":           _road_color(r.condition_score),
                "notes":           r.notes,
                "geojson":         _geojson(r),
            }
            for r in roads_qs
        ],
        "floods": [
            {
                "id":          f.id,
                "name":        f.name,
                "risk_level":  f.risk_level,
                "risk_label":  f.get_risk_level_display(),
                "risk_score":  f.risk_score,
                "area_km2":    _js_num(f.area_km2),
                "rainfall_mm": _js_num(f.rainfall_mm),
                "color": {
                    "faible": "#22d3ee", "modere": "#3b82f6",
                    "eleve": "#f97316", "critique": "#dc2626"
                }.get(f.risk_level, "#3b82f6"),
                "geojson": _geojson(f),
            }
            for f in floods_qs
        ],
        "vegetation": [
            {
                "id":               v.id,
                "name":             v.name,
                "ndvi_value":       _js_num(v.ndvi_value),
                "coverage_percent": _js_num(v.coverage_percent),
                "density_class":    v.density_class,
                "density_label":    v.get_density_class_display(),
                "geojson":          _geojson(v),
            }
            for v in veg_qs
        ],
    }

    # KPI
    avg_val   = roads_qs.aggregate(avg=Avg("condition_score"))["avg"] or 0
    avg_score = round(float(avg_val), 1)

    center_lat = _js_num(selected_zone.lat_center if selected_zone else 5.35)
    center_lng = _js_num(selected_zone.lng_center if selected_zone else -4.00)

    # Alertes  (is_read au lieu de resolved)
    alerts = Alert.objects.filter(
        zone=selected_zone, is_read=False
    ).order_by("-created_at")[:20] if selected_zone else \
             Alert.objects.filter(is_read=False).order_by("-created_at")[:20]

    unread = Alert.objects.filter(is_read=False).count()

    # Graphiques
    dist = {"0-25": 0, "26-50": 0, "51-75": 0, "76-100": 0}
    for r in roads_qs:
        sc = r.condition_score or 0
        if sc <= 25:   dist["0-25"]   += 1
        elif sc <= 50: dist["26-50"]  += 1
        elif sc <= 75: dist["51-75"]  += 1
        else:          dist["76-100"] += 1

    chart_routes = {"labels": list(dist.keys()), "data": list(dist.values())}

    flood_lvl = {"faible": 0, "modere": 0, "eleve": 0, "critique": 0}
    for f in floods_qs:
        if f.risk_level in flood_lvl:
            flood_lvl[f.risk_level] += 1

    chart_floods = {
        "labels": ["Faible", "Modéré", "Élevé", "Critique"],
        "data":   list(flood_lvl.values()),
    }

    # KPI routes
    total_roads    = roads_qs.count()
    critical_roads = roads_qs.filter(condition_score__lt=40).count()
    road_health_pct = round(float(avg_score))   # avg_score est déjà /100

    # KPI inondations
    total_floods    = floods_qs.count()
    critical_floods = floods_qs.filter(risk_level__in=["eleve", "critique"]).count()
    avg_flood_val   = floods_qs.aggregate(avg=Avg("risk_score"))["avg"] or 0
    avg_flood_risk  = round(float(avg_flood_val))

    # KPI végétation
    total_veg  = veg_qs.count()
    dense_veg  = veg_qs.filter(density_class__in=["dense", "very_dense"]).count()
    avg_ndvi_v = veg_qs.aggregate(avg=Avg("ndvi_value"))["avg"] or 0
    avg_ndvi   = round(float(avg_ndvi_v), 3)

    context = {
        "zones":         zones,
        "selected_zone": selected_zone,   # objet Zone ou None
        "zone_code":     zone_code,       # string brut du GET — utilisé pour le <select>
        # JSON pour Leaflet / Chart.js
        "map_data_json":     json.dumps(map_data),
        "chart_routes_json": json.dumps(chart_routes),
        "chart_floods_json": json.dumps(chart_floods),
        "avg_score_json":    json.dumps(avg_score),
        "center_lat_json":   json.dumps(center_lat),
        "center_lng_json":   json.dumps(center_lng),
        # KPI routes
        "avg_road_score":  avg_score,
        "total_roads":     total_roads,
        "critical_roads":  critical_roads,
        "road_health_pct": road_health_pct,
        # KPI inondations
        "total_floods":    total_floods,
        "critical_floods": critical_floods,
        "avg_flood_risk":  avg_flood_risk,
        # KPI végétation
        "total_veg":       total_veg,
        "dense_veg":       dense_veg,
        "avg_ndvi":        avg_ndvi,
        # Alertes
        "recent_alerts": alerts,   # nommé recent_alerts pour matcher le template
        "unread_alerts": unread,
        # Méta
        "last_update":   timezone.now(),
        # GEE
        "gee_available":  _gee_available(),
        "zone_bbox_json": json.dumps(_zone_bbox(selected_zone)),
    }
    return render(request, "dashboard/index.html", context)


# ── API — Carte ────────────────────────────────────────────────────────────────

@require_GET
def api_map_data(request):
    zone_code     = request.GET.get("zone", "")
    selected_zone = Zone.objects.filter(code=zone_code).first() if zone_code else None

    roads_qs  = RoadSegment.objects.filter(zone=selected_zone) if selected_zone \
                else RoadSegment.objects.all()
    floods_qs = FloodRisk.objects.filter(zone=selected_zone) if selected_zone \
                else FloodRisk.objects.all()
    veg_qs    = VegetationDensity.objects.filter(zone=selected_zone) if selected_zone \
                else VegetationDensity.objects.all()

    return JsonResponse({
        "routes": [
            {"id": r.id, "name": r.name, "condition_score": r.condition_score,
             "status": r.status, "color": _road_color(r.condition_score),
             "geojson": _geojson(r)}
            for r in roads_qs
        ],
        "floods": [
            {"id": f.id, "name": f.name, "risk_level": f.risk_level,
             "risk_score": f.risk_score, "geojson": _geojson(f)}
            for f in floods_qs
        ],
        "vegetation": [
            {"id": v.id, "name": v.name, "ndvi_value": _js_num(v.ndvi_value),
             "density_class": v.density_class, "geojson": _geojson(v)}
            for v in veg_qs
        ],
    })


# ── API — Alertes ──────────────────────────────────────────────────────────────

@require_GET
def api_alerts(request):
    zone_code = request.GET.get("zone", "")
    qs = Alert.objects.filter(is_read=False)
    if zone_code:
        qs = qs.filter(zone__code=zone_code)
    qs = qs.order_by("-created_at")[:20]

    return JsonResponse({
        "count": qs.count(),
        "alerts": [
            {
                "id":       a.id,
                "title":    a.title,
                "message":  a.message,
                "severity": a.severity,
                "category": a.category,
                "lat":      _js_num(a.lat),
                "lng":      _js_num(a.lng),
                "created":  a.created_at.isoformat(),
            }
            for a in qs
        ],
    })


@require_GET
def api_mark_alert_read(request, alert_id):
    alert = get_object_or_404(Alert, id=alert_id)
    alert.is_read = True
    alert.save(update_fields=["is_read"])
    return JsonResponse({"ok": True})


# ── API — Stats zone ───────────────────────────────────────────────────────────

@require_GET
def api_zone_stats(request, zone_code):
    zone  = get_object_or_404(Zone, code=zone_code)
    roads = RoadSegment.objects.filter(zone=zone)
    avg   = roads.aggregate(avg=Avg("condition_score"))["avg"] or 0
    return JsonResponse({
        "zone_code":   zone_code,
        "avg_score":   round(float(avg), 1),
        "road_count":  roads.count(),
        "flood_count": FloodRisk.objects.filter(zone=zone).count(),
        "alert_count": Alert.objects.filter(zone=zone, is_read=False).count(),
    })


# ── API — Google Earth Engine (asynchrone) ─────────────────────────────────────

@require_GET
def api_gee_ndvi(request):
    zone_code = request.GET.get("zone", "")
    zone  = Zone.objects.filter(code=zone_code).first() if zone_code else None
    bbox  = _zone_bbox(zone)
    try:
        from .gee_integration import get_ndvi_stats
        data = get_ndvi_stats(bbox)
        return JsonResponse(data or {"error": "Aucune donnée NDVI"})
    except Exception as e:
        logger.error("[GEE NDVI] %s", e)
        return JsonResponse({"error": str(e)}, status=503)


@require_GET
def api_gee_flood(request):
    zone_code = request.GET.get("zone", "")
    zone  = Zone.objects.filter(code=zone_code).first() if zone_code else None
    bbox  = _zone_bbox(zone)
    try:
        from .gee_integration import get_flood_extent
        data = get_flood_extent(bbox)
        return JsonResponse(data or {"error": "Aucune donnée SAR"})
    except Exception as e:
        logger.error("[GEE FLOOD] %s", e)
        return JsonResponse({"error": str(e)}, status=503)


@require_GET
def api_gee_road(request):
    zone_code = request.GET.get("zone", "")
    zone  = Zone.objects.filter(code=zone_code).first() if zone_code else None
    bbox  = _zone_bbox(zone)
    try:
        from .gee_integration import get_road_surface_index
        data = get_road_surface_index(bbox)
        return JsonResponse(data or {"error": "Aucune donnée surface"})
    except Exception as e:
        logger.error("[GEE ROAD] %s", e)
        return JsonResponse({"error": str(e)}, status=503)