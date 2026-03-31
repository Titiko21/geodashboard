"""
GéoDash — views.py
Vue principale + API endpoints.
"""
import csv
import json
import logging
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_GET
from django.db.models import Avg
from django.utils import timezone

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
    delta = 0.5
    if not zone:
        return {"west": -4.50, "south": 5.10, "east": -3.50, "north": 5.85}
    return {
        "west":  zone.lng_center - delta,
        "south": zone.lat_center - delta,
        "east":  zone.lng_center + delta,
        "north": zone.lat_center + delta,
    }

def _gee_available():
    try:
        from .gee_integration import is_gee_available, init_gee
        if not is_gee_available():
            init_gee()
        return is_gee_available()
    except Exception:
        return False


# ── Vue principale ─────────────────────────────────────────────────────────────

def dashboard(request):
    zone_code = request.GET.get("zone", "")
    zones     = Zone.objects.all().order_by("name")

    selected_zone = None
    if zone_code:
        selected_zone = Zone.objects.filter(code=zone_code).first()

    roads_qs  = RoadSegment.objects.filter(zone=selected_zone) if selected_zone else RoadSegment.objects.all()
    floods_qs = FloodRisk.objects.filter(zone=selected_zone) if selected_zone else FloodRisk.objects.all()
    veg_qs    = VegetationDensity.objects.filter(zone=selected_zone) if selected_zone else VegetationDensity.objects.all()

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

    # Alertes
    alerts = Alert.objects.filter(
        zone=selected_zone, is_read=False
    ).order_by("-created_at")[:20] if selected_zone else \
             Alert.objects.filter(is_read=False).order_by("-created_at")[:20]
    unread = Alert.objects.filter(is_read=False).count()

    # Stats
    total_roads    = roads_qs.count()
    critical_roads = roads_qs.filter(condition_score__lt=40).count()
    road_health_pct = round(float(avg_score))

    critical_floods = floods_qs.filter(risk_level__in=["eleve", "critique"]).count()
    avg_flood_val   = floods_qs.aggregate(avg=Avg("risk_score"))["avg"] or 0
    avg_flood_risk  = round(float(avg_flood_val))

    dense_veg  = veg_qs.filter(density_class__in=["dense", "very_dense"]).count()
    avg_ndvi_v = veg_qs.aggregate(avg=Avg("ndvi_value"))["avg"] or 0
    avg_ndvi   = round(float(avg_ndvi_v), 3)

    context = {
        "zones":         zones,
        "selected_zone": selected_zone,
        "zone_code":     zone_code,
        "map_data_json":     json.dumps(map_data),
        "avg_score_json":    json.dumps(avg_score),
        "center_lat_json":   json.dumps(center_lat),
        "center_lng_json":   json.dumps(center_lng),
        "avg_road_score":  avg_score,
        "total_roads":     total_roads,
        "critical_roads":  critical_roads,
        "road_health_pct": road_health_pct,
        "critical_floods": critical_floods,
        "avg_flood_risk":  avg_flood_risk,
        "dense_veg":       dense_veg,
        "avg_ndvi":        avg_ndvi,
        "recent_alerts":   alerts,
        "unread_alerts":   unread,
        "last_update":     timezone.now(),
        "gee_available":   _gee_available(),
        "zone_bbox_json":  json.dumps(_zone_bbox(selected_zone)),
    }
    return render(request, "dashboard/index.html", context)


# ── API — Carte ────────────────────────────────────────────────────────────────

@require_GET
def api_map_data(request):
    zone_code     = request.GET.get("zone", "")
    selected_zone = Zone.objects.filter(code=zone_code).first() if zone_code else None

    roads_qs  = RoadSegment.objects.filter(zone=selected_zone) if selected_zone else RoadSegment.objects.all()
    floods_qs = FloodRisk.objects.filter(zone=selected_zone) if selected_zone else FloodRisk.objects.all()
    veg_qs    = VegetationDensity.objects.filter(zone=selected_zone) if selected_zone else VegetationDensity.objects.all()

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


# ── API — Export alertes CSV ───────────────────────────────────────────────────

@require_GET
def api_alerts_export(request):
    zone_code = request.GET.get("zone", "")
    qs = Alert.objects.filter(is_read=False).order_by("-created_at")
    if zone_code:
        qs = qs.filter(zone__code=zone_code)

    filename = f"alertes_{zone_code or 'toutes'}_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")

    writer = csv.writer(response, delimiter=";")
    writer.writerow(["ID", "Titre", "Message", "Severite", "Categorie", "Zone", "Latitude", "Longitude", "Date creation"])

    for a in qs:
        writer.writerow([
            a.id, a.title, a.message,
            a.get_severity_display(), a.get_category_display(),
            a.zone.name if a.zone else "",
            _js_num(a.lat), _js_num(a.lng),
            a.created_at.strftime("%d/%m/%Y %H:%M"),
        ])
    return response


# ── API — Export routes GeoJSON ────────────────────────────────────────────────

@require_GET
def api_roads_export(request):
    zone_code     = request.GET.get("zone", "")
    selected_zone = Zone.objects.filter(code=zone_code).first() if zone_code else None
    qs = RoadSegment.objects.filter(zone=selected_zone) if selected_zone else RoadSegment.objects.all()

    features = []
    for r in qs:
        geo = _geojson(r)
        if not geo:
            continue
        features.append({
            "type": "Feature",
            "geometry": geo,
            "properties": {
                "id": r.id, "name": r.name, "status": r.status,
                "condition_score": r.condition_score, "surface_type": r.surface_type,
                "zone": r.zone.name if r.zone else "",
                "zone_code": r.zone.code if r.zone else "",
            },
        })

    geojson_data = {
        "type": "FeatureCollection",
        "generated": timezone.now().isoformat(),
        "features": features,
    }

    filename = f"routes_{zone_code or 'toutes'}_{timezone.now().strftime('%Y%m%d_%H%M')}.geojson"
    response = HttpResponse(
        json.dumps(geojson_data, ensure_ascii=False, indent=2),
        content_type="application/geo+json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


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


# ── API — Google Earth Engine ──────────────────────────────────────────────────

from .gee_integration import get_ndvi_stats, get_flood_extent, get_road_surface_index


@require_GET
def api_gee_ndvi(request):
    zone_code = request.GET.get("zone", "").strip()
    if not zone_code:
        return JsonResponse({"error": "Paramètre 'zone' requis."}, status=400)

    zone = get_object_or_404(Zone, code=zone_code)
    bbox = _zone_bbox(zone)

    try:
        data = get_ndvi_stats(bbox)
    except Exception as exc:
        logger.error("[GEE NDVI] Erreur zone %s : %s", zone_code, exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({"error": "Aucune image disponible.", "no_data": True, "zone": zone_code, "tiles_url": None})

    data["zone"] = zone_code
    return JsonResponse(data)


@require_GET
def api_gee_flood(request):
    zone_code = request.GET.get("zone", "").strip()
    if not zone_code:
        return JsonResponse({"error": "Paramètre 'zone' requis."}, status=400)

    zone = get_object_or_404(Zone, code=zone_code)
    bbox = _zone_bbox(zone)

    try:
        data = get_flood_extent(bbox)
    except Exception as exc:
        logger.error("[GEE Flood] Erreur zone %s : %s", zone_code, exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({"error": "Données SAR insuffisantes.", "no_data": True, "zone": zone_code, "tiles_url": None})

    data["zone"] = zone_code
    return JsonResponse(data)


@require_GET
def api_gee_road(request):
    zone_code = request.GET.get("zone", "").strip()
    if not zone_code:
        return JsonResponse({"error": "Paramètre 'zone' requis."}, status=400)

    zone = get_object_or_404(Zone, code=zone_code)
    bbox = _zone_bbox(zone)

    try:
        data = get_road_surface_index(bbox)
    except Exception as exc:
        logger.error("[GEE Road] Erreur zone %s : %s", zone_code, exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({"error": "Données Landsat insuffisantes.", "no_data": True, "zone": zone_code})

    data["zone"] = zone_code
    return JsonResponse(data)