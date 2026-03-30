"""
traffic_views.py — Vues API pour l'estimation du trafic.

Place ce fichier dans : dashboard/traffic_views.py
"""
import logging
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import Zone
from .traffic_estimator import estimate_zone_traffic

logger = logging.getLogger("dashboard")


@require_GET
def api_traffic_zone(request, zone_code):
    """
    GET /api/traffic/<zone_code>/
    Retourne l'estimation du trafic pour une zone spécifique.
    """
    zone = get_object_or_404(Zone, code=zone_code)
    data = estimate_zone_traffic(zone)
    return JsonResponse(data)


@require_GET
def api_traffic_all(request):
    """
    GET /api/traffic/
    Retourne un résumé du trafic pour toutes les zones.
    Paramètre optionnel : ?top=10 pour limiter le nombre de résultats.
    """
    top = int(request.GET.get("top", 0))
    zones = Zone.objects.all().order_by("name")

    results = []
    for zone in zones:
        data = estimate_zone_traffic(zone)
        results.append({
            "zone_code":     data["zone_code"],
            "zone_name":     data["zone_name"],
            "traffic_score": data["traffic_score"],
            "traffic_level": data["traffic_level"],
            "traffic_label": data["traffic_label"],
            "traffic_color": data["traffic_color"],
            "total_roads":   data["total_roads"],
            "capacity_index": data["capacity_index"],
            "viirs_score":   data["viirs_score"],
        })

    results.sort(key=lambda x: x["traffic_score"], reverse=True)

    if top > 0:
        results = results[:top]

    return JsonResponse({
        "count": len(results),
        "zones": results,
    })