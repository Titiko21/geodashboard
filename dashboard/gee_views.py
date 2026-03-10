"""
gee_views.py — Vues API pour l'intégration GEE
================================================
Ces vues s'ajoutent au fichier urls.py existant.
Elles permettent de :
  - Tester la connexion GEE depuis le dashboard
  - Déclencher une synchronisation via le navigateur
  - Voir le statut de la dernière synchronisation
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from django.core.cache import cache

from .models import Zone


@require_GET
def api_gee_status(request):
    """
    Teste et retourne le statut de la connexion GEE.
    GET /api/gee/status/
    """
    try:
        from .gee_client import test_connection
        ok, message = test_connection()
        return JsonResponse({
            'connected': ok,
            'message':   message,
            'checked_at': timezone.now().strftime('%d/%m/%Y %H:%M:%S'),
        })
    except ImportError:
        return JsonResponse({
            'connected': False,
            'message':   'earthengine-api non installé. Lancez : pip install earthengine-api',
        })
    except Exception as e:
        return JsonResponse({'connected': False, 'message': str(e)})


@require_POST
def api_gee_sync(request):
    """
    Déclenche une synchronisation GEE (tâche synchrone).
    POST /api/gee/sync/
    Body JSON optionnel : {"zone": "ABJ-N", "layer": "ndvi"}
    """

    import json
    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        body = {}

    zone_code = body.get('zone', '')
    layer     = body.get('layer', 'all')

    try:
        from .gee_client import (
            init_gee, get_ndvi_stats, get_flood_risk_stats,
            bbox_to_geojson,
        )
        from .models import VegetationDensity, FloodRisk, Alert

        init_gee()
        results = []
        errors  = []

        zones = Zone.objects.all()
        if zone_code:
            zones = zones.filter(code=zone_code)

        DELTA = 0.10
        for zone in zones:
            bounds = (
                zone.lng_center - DELTA, zone.lat_center - DELTA,
                zone.lng_center + DELTA, zone.lat_center + DELTA,
            )

            if layer in ('all', 'ndvi'):
                try:
                    ndvi_data = get_ndvi_stats(*bounds)
                    VegetationDensity.objects.update_or_create(
                        zone=zone, name=f'Végétation — {zone.name}',
                        defaults={
                            'ndvi_value':       ndvi_data['ndvi_mean'],
                            'density_class':    ndvi_data['density_class'],
                            'coverage_percent': ndvi_data['coverage_pct'],
                            'last_analyzed':    timezone.now(),
                            'geojson':          bbox_to_geojson(*bounds),
                        }
                    )
                    results.append(f'{zone.name} — NDVI : {ndvi_data["ndvi_mean"]}')
                except Exception as e:
                    errors.append(f'{zone.name} NDVI : {str(e)}')

            if layer in ('all', 'flood'):
                try:
                    flood_data = get_flood_risk_stats(*bounds)
                    FloodRisk.objects.update_or_create(
                        zone=zone, name=f'Zone inondation — {zone.name}',
                        defaults={
                            'risk_level':    flood_data['risk_level'],
                            'risk_score':    flood_data['risk_score'],
                            'area_km2':      round((2 * DELTA * 111) ** 2, 1),
                            'rainfall_mm':   flood_data['rainfall_mm'],
                            'last_analyzed': timezone.now(),
                            'geojson':       bbox_to_geojson(*bounds),
                        }
                    )
                    results.append(f'{zone.name} — Risque : {flood_data["risk_score"]}/100')
                except Exception as e:
                    errors.append(f'{zone.name} inondation : {str(e)}')

        return JsonResponse({
            'status':    'ok' if not errors else 'partial',
            'synced':    len(results),
            'results':   results,
            'errors':    errors,
            'synced_at': timezone.now().strftime('%d/%m/%Y %H:%M:%S'),
        })

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
