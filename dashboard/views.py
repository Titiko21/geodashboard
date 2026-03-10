import json
import math
from decimal import Decimal

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Avg
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder

from .models import Zone, RoadSegment, FloodRisk, VegetationDensity, Alert
from .constants import ROAD_COLORS, FLOOD_COLORS, VEG_COLORS


# ─────────────────────────────────────────────────────────────
#  UTILITAIRES
# ─────────────────────────────────────────────────────────────

class SafeEncoder(DjangoJSONEncoder):
    """Decimal → float, neutralise NaN/Infinity."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            v = float(obj)
            return None if (math.isnan(v) or math.isinf(v)) else v
        return super().default(obj)


def safe_float(val, default=0.0):
    """Float sûr : jamais NaN, jamais None."""
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else round(f, 4)
    except (TypeError, ValueError):
        return default


def to_geojson(obj):
    """Normalise un champ geojson (dict ou string JSON)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    try:
        return json.loads(obj)
    except (json.JSONDecodeError, TypeError):
        return None


def js_num(val):
    """
    Sérialise un nombre pour injection JS via json_script.
    json.dumps garantit le point décimal — évite le bug locale fr (0,0).
    """
    return json.dumps(float(val), cls=SafeEncoder)


# ─────────────────────────────────────────────────────────────
#  KPIs
# ─────────────────────────────────────────────────────────────

def _get_road_kpis(road_qs):
    avg_score = road_qs.aggregate(avg=Avg('condition_score'))['avg'] or 0
    return {
        'total_roads':     road_qs.count(),
        'critical_roads':  road_qs.filter(status__in=['critique', 'ferme']).count(),
        'avg_road_score':  round(safe_float(avg_score), 1),
        'road_health_pct': int(safe_float(avg_score)),
        'road_dist': {
            'bon':      road_qs.filter(status='bon').count(),
            'degrade':  road_qs.filter(status='degrade').count(),
            'critique': road_qs.filter(status='critique').count(),
            'ferme':    road_qs.filter(status='ferme').count(),
        },
    }


def _get_flood_kpis(flood_qs):
    avg_risk = flood_qs.aggregate(avg=Avg('risk_score'))['avg'] or 0
    return {
        'critical_floods': flood_qs.filter(risk_level__in=['eleve', 'critique']).count(),
        'avg_flood_risk':  round(safe_float(avg_risk), 1),
        'flood_dist': {
            'faible':   flood_qs.filter(risk_level='faible').count(),
            'modere':   flood_qs.filter(risk_level='modere').count(),
            'eleve':    flood_qs.filter(risk_level='eleve').count(),
            'critique': flood_qs.filter(risk_level='critique').count(),
        },
    }


def _get_veg_kpis(veg_qs):
    avg_ndvi = veg_qs.aggregate(avg=Avg('ndvi_value'))['avg'] or 0
    return {
        'avg_ndvi':  round(safe_float(avg_ndvi), 3),
        'dense_veg': veg_qs.filter(density_class__in=['dense', 'very_dense']).count(),
    }


def _build_map_data(road_qs, flood_qs, veg_qs):
    return {
        'routes': [
            {
                'name':            r.name,
                'status':          r.status,
                'status_label':    r.get_status_display(),
                'condition_score': safe_float(r.condition_score),
                'surface_type':    r.surface_type or '',
                'notes':           r.notes or '',
                'color':           ROAD_COLORS.get(r.status, '#6b7280'),
                'geojson':         to_geojson(r.geojson),
            }
            for r in road_qs if r.geojson
        ],
        'floods': [
            {
                'name':        f.name,
                'risk_level':  f.risk_level,
                'risk_label':  f.get_risk_level_display(),
                'risk_score':  safe_float(f.risk_score),
                'area_km2':    safe_float(f.area_km2),
                'rainfall_mm': safe_float(f.rainfall_mm),
                'color':       FLOOD_COLORS.get(f.risk_level, '#22d3ee'),
                'geojson':     to_geojson(f.geojson),
            }
            for f in flood_qs if f.geojson
        ],
        'vegetation': [
            {
                'name':             v.name,
                'ndvi_value':       safe_float(v.ndvi_value),
                'coverage_percent': safe_float(v.coverage_percent),
                'density_class':    v.density_class or '',
                'density_label':    v.get_density_class_display(),
                'change':           safe_float(v.change_vs_previous),
                'color':            VEG_COLORS.get(v.density_class, '#4ade80'),
                'geojson':          to_geojson(v.geojson),
            }
            for v in veg_qs if v.geojson
        ],
    }


# ─────────────────────────────────────────────────────────────
#  VUE PRINCIPALE
# ─────────────────────────────────────────────────────────────

def dashboard(request):
    zones              = Zone.objects.all()
    selected_zone_code = request.GET.get('zone', '')

    current_zone = None
    if selected_zone_code:
        try:
            current_zone = Zone.objects.get(code=selected_zone_code)
        except Zone.DoesNotExist:
            pass

    road_qs  = RoadSegment.objects.select_related('zone').all()
    flood_qs = FloodRisk.objects.select_related('zone').all()
    veg_qs   = VegetationDensity.objects.select_related('zone').all()

    if current_zone:
        road_qs  = road_qs.filter(zone=current_zone)
        flood_qs = flood_qs.filter(zone=current_zone)
        veg_qs   = veg_qs.filter(zone=current_zone)

    road_kpis  = _get_road_kpis(road_qs)
    flood_kpis = _get_flood_kpis(flood_qs)
    veg_kpis   = _get_veg_kpis(veg_qs)

    alert_qs      = Alert.objects.filter(is_read=False)
    if current_zone:
        alert_qs  = alert_qs.filter(zone=current_zone)
    unread_alerts = alert_qs.count()
    recent_alerts = alert_qs.order_by('-created_at')[:10]

    map_data = _build_map_data(road_qs, flood_qs, veg_qs)

    rd = road_kpis['road_dist']
    chart_routes = {
        'labels': ['Bon', 'Dégradé', 'Critique', 'Fermé'],
        'values': [rd['bon'], rd['degrade'], rd['critique'], rd['ferme']],
        'colors': ['#22c55e', '#f97316', '#ef4444', '#6b7280'],
    }
    fd = flood_kpis['flood_dist']
    chart_floods = {
        'labels': ['Faible', 'Modéré', 'Élevé', 'Critique'],
        'values': [fd['faible'], fd['modere'], fd['eleve'], fd['critique']],
        'colors': ['#22d3ee', '#3b82f6', '#f97316', '#dc2626'],
    }

    if current_zone:
        center_lat = safe_float(current_zone.lat_center, 5.35)
        center_lng = safe_float(current_zone.lng_center, -4.00)
    elif zones.exists():
        lats = [safe_float(z.lat_center) for z in zones if z.lat_center]
        lngs = [safe_float(z.lng_center) for z in zones if z.lng_center]
        center_lat = round(sum(lats) / len(lats), 4) if lats else 5.35
        center_lng = round(sum(lngs) / len(lngs), 4) if lngs else -4.00
    else:
        center_lat, center_lng = 5.35, -4.00

    avg_score = road_kpis['avg_road_score']

    context = {
        'zones':           zones,
        'selected_zone':   selected_zone_code,
        'current_zone':    current_zone,
        'total_roads':     road_kpis['total_roads'],
        'critical_roads':  road_kpis['critical_roads'],
        'avg_road_score':  avg_score,
        'road_health_pct': road_kpis['road_health_pct'],
        'critical_floods': flood_kpis['critical_floods'],
        'avg_flood_risk':  flood_kpis['avg_flood_risk'],
        'avg_ndvi':        veg_kpis['avg_ndvi'],
        'dense_veg':       veg_kpis['dense_veg'],
        'unread_alerts':   unread_alerts,
        'recent_alerts':   recent_alerts,
        'last_update':     timezone.now(),
        'center_lat':      center_lat,
        'center_lng':      center_lng,
        # Valeurs JS — toutes via json.dumps, jamais de locale fr
        'map_data_json':     json.dumps(map_data,       cls=SafeEncoder, ensure_ascii=False),
        'chart_routes_json': json.dumps(chart_routes,   cls=SafeEncoder),
        'chart_floods_json': json.dumps(chart_floods,   cls=SafeEncoder),
        'avg_score_json':    js_num(avg_score),
        'center_lat_json':   js_num(center_lat),
        'center_lng_json':   js_num(center_lng),
    }
    return render(request, 'dashboard/index.html', context)


# ─────────────────────────────────────────────────────────────
#  API — CARTE
# ─────────────────────────────────────────────────────────────

@require_GET
def api_map_data(request):
    zone_code = request.GET.get('zone', '')
    layer     = request.GET.get('layer', 'all')
    features  = []

    if layer in ('roads', 'all'):
        qs = RoadSegment.objects.select_related('zone')
        if zone_code:
            qs = qs.filter(zone__code=zone_code)
        for r in qs:
            geo = to_geojson(r.geojson)
            if geo:
                features.append({
                    'type': 'Feature', 'geometry': geo,
                    'properties': {
                        'type': 'road', 'id': r.id, 'name': r.name,
                        'status': r.status, 'status_label': r.get_status_display(),
                        'score': safe_float(r.condition_score),
                        'surface_type': r.surface_type or '',
                        'color': ROAD_COLORS.get(r.status, '#6b7280'),
                        'zone': r.zone.name,
                        'analyzed': r.last_analyzed.strftime('%d/%m/%Y %H:%M') if r.last_analyzed else '',
                        'notes': r.notes or '',
                    }
                })

    if layer in ('floods', 'all'):
        qs = FloodRisk.objects.select_related('zone')
        if zone_code:
            qs = qs.filter(zone__code=zone_code)
        for f in qs:
            geo = to_geojson(f.geojson)
            if geo:
                features.append({
                    'type': 'Feature', 'geometry': geo,
                    'properties': {
                        'type': 'flood', 'id': f.id, 'name': f.name,
                        'risk_level': f.risk_level, 'risk_label': f.get_risk_level_display(),
                        'risk_score': safe_float(f.risk_score),
                        'area_km2': safe_float(f.area_km2),
                        'rainfall': safe_float(f.rainfall_mm),
                        'color': FLOOD_COLORS.get(f.risk_level, '#22d3ee'),
                        'zone': f.zone.name,
                        'analyzed': f.last_analyzed.strftime('%d/%m/%Y %H:%M') if f.last_analyzed else '',
                    }
                })

    if layer in ('vegetation', 'all'):
        qs = VegetationDensity.objects.select_related('zone')
        if zone_code:
            qs = qs.filter(zone__code=zone_code)
        for v in qs:
            geo = to_geojson(v.geojson)
            if geo:
                features.append({
                    'type': 'Feature', 'geometry': geo,
                    'properties': {
                        'type': 'vegetation', 'id': v.id, 'name': v.name,
                        'ndvi': safe_float(v.ndvi_value),
                        'density_class': v.density_class or '',
                        'density_label': v.get_density_class_display(),
                        'coverage_pct': safe_float(v.coverage_percent),
                        'change': safe_float(v.change_vs_previous),
                        'color': VEG_COLORS.get(v.density_class, '#4ade80'),
                        'zone': v.zone.name,
                        'analyzed': v.last_analyzed.strftime('%d/%m/%Y %H:%M') if v.last_analyzed else '',
                    }
                })

    return JsonResponse({'type': 'FeatureCollection', 'features': features}, encoder=SafeEncoder)


# ─────────────────────────────────────────────────────────────
#  API — ALERTES
# ─────────────────────────────────────────────────────────────

@require_GET
def api_alerts(request):
    alerts = Alert.objects.filter(is_read=False).order_by('-created_at')[:20]
    data = [
        {
            'id': a.id, 'title': a.title, 'message': a.message,
            'severity': a.severity, 'category': a.category,
            'cat_label': a.get_category_display(),
            'created_at': a.created_at.strftime('%d/%m/%Y %H:%M'),
            'lat': safe_float(a.lat), 'lng': safe_float(a.lng),
            'zone': a.zone.name if a.zone else None,
        }
        for a in alerts
    ]
    return JsonResponse({'alerts': data, 'count': len(data)}, encoder=SafeEncoder)


@require_POST
def api_mark_alert_read(request, alert_id):
    alert = get_object_or_404(Alert, id=alert_id)
    alert.is_read = True
    alert.save()
    return JsonResponse({'status': 'ok', 'remaining': Alert.objects.filter(is_read=False).count()})


# ─────────────────────────────────────────────────────────────
#  API — STATS PAR ZONE
# ─────────────────────────────────────────────────────────────

@require_GET
def api_zone_stats(request, zone_code):
    zone   = get_object_or_404(Zone, code=zone_code)
    roads  = RoadSegment.objects.filter(zone=zone)
    floods = FloodRisk.objects.filter(zone=zone)
    vegs   = VegetationDensity.objects.filter(zone=zone)

    return JsonResponse({
        'zone': {'name': zone.name, 'code': zone.code},
        'roads': {
            'total': roads.count(),
            'avg_score': safe_float(roads.aggregate(avg=Avg('condition_score'))['avg']),
            'by_status': {s: roads.filter(status=s).count() for s in ('bon', 'degrade', 'critique', 'ferme')},
        },
        'floods': {
            'total': floods.count(),
            'avg_risk': safe_float(floods.aggregate(avg=Avg('risk_score'))['avg']),
            'total_area': round(sum(safe_float(f.area_km2) for f in floods), 2),
            'by_level': {lvl: floods.filter(risk_level=lvl).count() for lvl in ('faible', 'modere', 'eleve', 'critique')},
        },
        'vegetation': {
            'total': vegs.count(),
            'avg_ndvi': safe_float(vegs.aggregate(avg=Avg('ndvi_value'))['avg']),
            'avg_coverage': safe_float(vegs.aggregate(avg=Avg('coverage_percent'))['avg']),
        },
        'alerts': Alert.objects.filter(zone=zone, is_read=False).count(),
    }, encoder=SafeEncoder)