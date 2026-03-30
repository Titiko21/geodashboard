"""
health.py — Endpoint de health check pour Docker et monitoring.

Ajouter dans urls.py :
    from dashboard.health import health_check
    urlpatterns = [
        path('health/', health_check, name='health_check'),
        ...
    ]
"""
import json
import logging
import os

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone

logger = logging.getLogger("dashboard")


def health_check(request):
    """
    GET /health/ — vérifie :
      1. Connexion PostgreSQL
      2. Résolution DNS du host DB
      3. Accessibilité GEE (optionnel)
      4. Nombre de zones / routes en base
    """
    status = "healthy"
    checks = {}

    # ── 1. PostgreSQL ──
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)[:200]}
        status = "unhealthy"

    # ── 2. DNS du host DB ──
    db_host = os.environ.get("POSTGRES_HOST", "db")
    try:
        import socket
        addr = socket.getaddrinfo(db_host, 5432)
        checks["dns_db"] = {"status": "ok", "host": db_host, "resolved": addr[0][4][0]}
    except socket.gaierror as e:
        checks["dns_db"] = {"status": "error", "host": db_host, "detail": str(e)}
        status = "unhealthy"

    # ── 3. GEE (optionnel, ne fait pas échouer le health check) ──
    gee_key = os.environ.get("GEE_KEY_FILE", "")
    if gee_key:
        if os.path.isfile(gee_key):
            checks["gee_credentials"] = {"status": "ok", "key_file": gee_key}
        else:
            checks["gee_credentials"] = {"status": "warning", "detail": f"Fichier introuvable: {gee_key}"}
    else:
        checks["gee_credentials"] = {"status": "skipped", "detail": "GEE_KEY_FILE non configuré"}

    # ── 4. Stats rapides ──
    try:
        from dashboard.models import Zone, RoadSegment
        zone_count = Zone.objects.count()
        road_count = RoadSegment.objects.count()
        checks["data"] = {
            "zones": zone_count,
            "road_segments": road_count,
            "populated": road_count > 0,
        }
    except Exception as e:
        checks["data"] = {"status": "error", "detail": str(e)[:200]}

    http_status = 200 if status == "healthy" else 503
    return JsonResponse(
        {
            "status": status,
            "timestamp": timezone.now().isoformat(),
            "checks": checks,
        },
        status=http_status,
    )
