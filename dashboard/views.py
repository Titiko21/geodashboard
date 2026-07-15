"""
GéoDash — views.py
Synchronisé avec models.py :
  RoadSegment / FloodRisk / VegetationDensity / Alert

Patch GEE appliqué :
  - api_gee_ndvi / api_gee_flood / api_gee_road retournent désormais
    200 + {"error": ..., "no_data": True} quand GEE renvoie None,
    400 si le paramètre zone est absent, 500 sur erreur inattendue.
  - _zone_bbox : delta porté à 0.5° (≈ 55 km) pour les analyses GEE.
"""
import csv
import json
import logging
import urllib.request
from urllib.parse import urlencode
from django.conf       import settings
from django.shortcuts  import render, get_object_or_404
from django.http       import JsonResponse, HttpResponse
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
    """
    Retourne le GeoJSON de l'objet (LineString / Polygon).

    Stratégie post-migration PostGIS :
      1. Priorité au champ `geom` (GeometryField PostGIS) → source de vérité.
         `geom.geojson` est une property native Django qui sérialise via GDAL,
         identique au format attendu par le frontend Leaflet.
      2. Fallback sur le `geojson` JSONField historique pour les lignes legacy
         dont la géométrie n'a pas pu être migrée (322 polygones FloodRisk
         dégénérés au moment de la data migration 0006).
      3. None si ni l'un ni l'autre ne sont exploitables.
    """
    geom = getattr(obj, 'geom', None)
    if geom is not None:
        try:
            # `geom.geojson` renvoie une string JSON ; on la parse en dict pour
            # rester cohérent avec ce qu'attendent les sérialiseurs JSON.
            return json.loads(geom.geojson)
        except Exception:
            pass  # tombe sur le fallback JSONField

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
    Bbox approximative autour du centroïde d'une Zone (legacy).
    Conservée pour rétrocompatibilité ; les analyses GEE utilisent désormais
    `_resolve_gee_geom` qui prend la géométrie admin réelle.
    """
    delta = 0.5
    if not zone:
        return {"west": -4.50, "south": 5.10, "east": -3.50, "north": 5.85}
    return {
        "west":  zone.lng_center - delta,
        "south": zone.lat_center - delta,
        "east":  zone.lng_center + delta,
        "north": zone.lat_center + delta,
    }


def _resolve_gee_geom(request):
    """
    Résout la géométrie GEE pertinente selon les filtres URL :
      - ?admin=<level>:<code>  → polygone réel du district/région
      - ?zone=<code>           → cercle de 10 km autour du centroïde
      - sinon                  → bbox approximative Côte d'Ivoire

    Renvoie un tuple (geom_geojson_str, scope_label) où geom_geojson_str
    est une chaîne JSON GeoJSON, et scope_label le nom lisible.
    """
    import json as _json

    # 1) Filtre admin → géométrie réelle
    admin_level, admin_obj = _parse_admin_filter(request)
    if admin_obj and admin_obj.geom is not None:
        return admin_obj.geom.geojson, admin_obj.name

    # 2) Zone → cercle 10 km autour du centroïde via PostGIS
    zone_code = (request.GET.get("zone") or "").strip()
    if zone_code:
        zone = Zone.objects.filter(code=zone_code).first()
        if zone:
            from django.contrib.gis.geos import Point
            from django.db import connection
            pt = Point(zone.lng_center, zone.lat_center, srid=4326)
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT ST_AsGeoJSON(ST_Buffer(%s::geography, 10000)::geometry)",
                    [pt.wkt],
                )
                row = cur.fetchone()
            if row and row[0]:
                return row[0], zone.name

    # 3) Fallback Côte d'Ivoire
    civ_bbox = _json.dumps({
        "type": "Polygon",
        "coordinates": [[
            [-8.6, 4.3], [-2.5, 4.3], [-2.5, 10.7], [-8.6, 10.7], [-8.6, 4.3]
        ]],
    })
    return civ_bbox, "Côte d'Ivoire"

def _gee_available():
    try:
        from .gee_integration import _gee_initialized, init_gee
        if not _gee_initialized:
            init_gee()
        return True
    except Exception:
        return False


def _parse_admin_filter(request):
    """
    Lit le paramètre `?admin=<level>:<code>` (sélecteur unifié) et résout
    l'entité admin correspondante.

    Niveaux supportés : district, region, departement, sousprefecture, commune.
    Renvoie (level, obj) ou (None, None) si paramètre absent / invalide.
    """
    from admin_divisions.models import (
        Commune, Departement, District, Region, SousPrefecture,
    )

    raw = (request.GET.get("admin") or "").strip()
    if not raw or ":" not in raw:
        return None, None

    level, code = raw.split(":", 1)
    MODEL_MAP = {
        "district":       District,
        "region":         Region,
        "departement":    Departement,
        "sousprefecture": SousPrefecture,
        "commune":        Commune,
    }
    Model = MODEL_MAP.get(level)
    if not Model:
        return None, None
    try:
        return level, Model.objects.get(code=code)
    except Model.DoesNotExist:
        return None, None


def _filter_querysets_by_admin(roads_qs, floods_qs, veg_qs, admin_obj):
    """
    Filtre spatial EXACT : chaque objet est rattaché à UNE SEULE entité admin
    via son point représentatif (`PointOnSurface`) contenu dans le polygone.

    On évite ainsi qu'une route traversant une frontière apparaisse dans
    plusieurs zones — ce que faisait l'ancien `ST_Intersects`, source du
    « mélange » entre la zone ciblée et ses voisines.
    """
    if admin_obj is None or getattr(admin_obj, "geom", None) is None:
        return roads_qs, floods_qs, veg_qs
    from django.contrib.gis.db.models.functions import PointOnSurface
    geom = admin_obj.geom
    roads_qs  = roads_qs.filter(geom__isnull=False).annotate(_pt=PointOnSurface("geom")).filter(_pt__within=geom)
    floods_qs = floods_qs.filter(geom__isnull=False).annotate(_pt=PointOnSurface("geom")).filter(_pt__within=geom)
    veg_qs    = veg_qs.filter(geom__isnull=False).annotate(_pt=PointOnSurface("geom")).filter(_pt__within=geom)
    return roads_qs, floods_qs, veg_qs


# Buffer autour du centroïde d'une Zone pour le filtrage spatial. La Zone
# n'ayant pas de polygone propre (juste lat/lng), on définit une emprise
# raisonnable d'une commune urbaine type Côte d'Ivoire.
ZONE_SPATIAL_BUFFER_M = 2000  # 2 km autour du centroïde


def _filter_querysets_by_zone_spatial(roads_qs, floods_qs, veg_qs, zone):
    """
    Restreint spatialement les querysets à un buffer autour du centroïde
    de la Zone. Évite que des routes/inondations/végétation taguées avec
    le code de la zone mais géographiquement chez la commune voisine
    polluent l'affichage.
    """
    if zone is None:
        return roads_qs, floods_qs, veg_qs
    from django.contrib.gis.geos import Point
    from django.contrib.gis.measure import D
    centroid = Point(zone.lng_center, zone.lat_center, srid=4326)
    roads_qs  = roads_qs.filter(geom__distance_lte=(centroid, D(m=ZONE_SPATIAL_BUFFER_M)))
    floods_qs = floods_qs.filter(geom__distance_lte=(centroid, D(m=ZONE_SPATIAL_BUFFER_M)))
    veg_qs    = veg_qs.filter(geom__distance_lte=(centroid, D(m=ZONE_SPATIAL_BUFFER_M)))
    return roads_qs, floods_qs, veg_qs


# Routes stratégiques (axes structurants) — affichées par défaut pour une
# lecture décisionnelle non encombrée. Les voies résidentielles/service/track
# sont masquées sauf si ?focus=all.
STRATEGIC_HIGHWAY_REGEX = r"Type OSM : (motorway|trunk|primary|secondary)"


def _apply_strategic_filter(roads_qs, request):
    """
    Filtre les routes pour ne garder que les axes structurants (motorway,
    trunk, primary, secondary) par défaut. Passer ?focus=all pour afficher
    tout le réseau y compris les voies résidentielles et tertiaires.
    """
    focus = (request.GET.get("focus") or "major").strip().lower()
    if focus == "all":
        return roads_qs
    # Booléen indexé `is_strategic` (peuplé à l'import) — bien plus rapide que
    # l'ancien regex `notes__iregex` (scan séquentiel sur ~128k lignes).
    return roads_qs.filter(is_strategic=True)


def _filter_zones_by_admin(zones_qs, admin_obj):
    """
    Restreint un queryset de Zone aux centroïdes contenus dans admin.geom.

    Volontairement en Python (et pas en SQL) parce qu'on a au plus 170 zones,
    et que Zone n'a pas de GeometryField (juste lat_center/lng_center). Coût
    négligeable, code lisible.
    """
    if admin_obj is None or getattr(admin_obj, "geom", None) is None:
        return zones_qs
    from django.contrib.gis.geos import Point
    poly = admin_obj.geom
    ids_inside = [
        z.id for z in zones_qs
        if poly.contains(Point(z.lng_center, z.lat_center, srid=4326))
    ]
    return zones_qs.filter(id__in=ids_inside)


def _build_admin_tree(districts, all_zones):
    """
    Construit l'arbre hiérarchique du panneau gauche :
        Côte d'Ivoire → District → Zone (commune/ville)

    Chaque zone est rattachée à un district par test ST_Contains de son
    centroïde. Les zones sans rattachement (centroïde hors de tous les
    districts à cause d'imprécisions) tombent dans un groupe "Non classées".

    Format renvoyé (consommé par le template) :
        [
          {
            "district":      <District>,
            "select_value":  "district:CIV-DIS-LAGUNES",
            "zones": [
                {"zone": <Zone>, "select_value": "zone:ABJ"},
                ...
            ],
          }, ...
        ]
    """
    from django.contrib.gis.geos import Point

    nodes = []
    unclassified = []

    # Pré-calcul des Points (1 par zone) pour éviter les recréations en boucle
    zones_with_pt = [
        (z, Point(z.lng_center, z.lat_center, srid=4326))
        for z in all_zones
    ]

    # Pour chaque district, on identifie ses zones (Point in Polygon)
    classified_ids = set()
    for d in districts:
        bucket = []
        if d.geom is not None:
            for z, pt in zones_with_pt:
                if d.geom.contains(pt):
                    bucket.append({"zone": z, "select_value": f"zone:{z.code}"})
                    classified_ids.add(z.id)
        nodes.append({
            "district":      d,
            "select_value":  f"district:{d.code}",
            "zones":         sorted(bucket, key=lambda x: x["zone"].name),
        })

    # Zones non classées (hors géométries des districts)
    for z, _pt in zones_with_pt:
        if z.id not in classified_ids:
            unclassified.append({"zone": z, "select_value": f"zone:{z.code}"})

    if unclassified:
        nodes.append({
            "district":     None,
            "select_value": "",
            "zones":        sorted(unclassified, key=lambda x: x["zone"].name),
            "unclassified": True,
        })

    return nodes


def _build_admin_categories(admin_filter_value=""):
    """
    Sépare les entités du découpage en CATÉGORIES par type (Villes, Communes,
    Districts, Régions, Départements, Sous-préfectures) pour le panneau latéral.
    Chaque entité porte `select_value` ("level:code") → un clic applique le
    filtre admin exact. La catégorie contenant l'entité sélectionnée est ouverte ;
    Villes / Communes le sont par défaut (entités de tête, peu nombreuses).
    """
    from admin_divisions.models import (
        Commune, Departement, District, Region, SousPrefecture, Ville,
    )
    # Ordre = hiérarchie territoriale descendante (blueprint architecture).
    # Model=None → niveau prévu par l'architecture mais pas encore alimenté
    # (rubrique visible, vide) : l'interface est prête à accueillir les données
    # sans refonte. C'est le cas des Quartiers aujourd'hui.
    specs = [
        ("Districts",        "district",       District,       False),
        ("Régions",          "region",         Region,         False),
        ("Départements",     "departement",    Departement,    False),
        ("Sous-préfectures", "sousprefecture", SousPrefecture, False),
        ("Villes",           "ville",          Ville,          True),
        ("Communes",         "commune",        Commune,        True),
        ("Quartiers",        "quartier",       None,           False),
    ]
    cats = []
    for label, level, Model, open_default in specs:
        entities = [] if Model is None else [
            {"name": name, "code": code, "select_value": f"{level}:{code}"}
            for code, name in Model.objects.order_by("name").values_list("code", "name")
        ]
        has_selected = any(e["select_value"] == admin_filter_value for e in entities)
        cats.append({
            "label":    label,
            "level":    level,
            "entities": entities,
            "count":    len(entities),
            "open":     open_default or has_selected,
        })
    return cats


# ── Vue principale ─────────────────────────────────────────────────────────────

def dashboard(request):
    # ── 1. Parse du filtre admin unifié (?admin=<level>:<code>) ─────────
    admin_level, admin_obj = _parse_admin_filter(request)
    admin_label = admin_obj.name if admin_obj else None

    # ── 2. Liste des zones, filtrée par admin si applicable ─────────────
    zones = _filter_zones_by_admin(Zone.objects.all().order_by("name"), admin_obj)

    # ── 3. Zone sélectionnée : ignorée si en dehors du filtre admin ─────
    zone_code = request.GET.get("zone", "")
    selected_zone = None
    if zone_code:
        selected_zone = zones.filter(code=zone_code).first()
        if not selected_zone:
            zone_code = ""  # reset si la zone n'est plus dans la liste

    # ── 4. QuerySets de base ────────────────────────────────────────────
    roads_qs  = RoadSegment.objects.filter(zone=selected_zone)        if selected_zone else RoadSegment.objects.all()
    floods_qs = FloodRisk.objects.filter(zone=selected_zone)          if selected_zone else FloodRisk.objects.all()
    veg_qs    = VegetationDensity.objects.filter(zone=selected_zone)  if selected_zone else VegetationDensity.objects.all()

    # ── 5a. Filtre spatial admin (ST_Intersects via PostGIS) ────────────
    roads_qs, floods_qs, veg_qs = _filter_querysets_by_admin(
        roads_qs, floods_qs, veg_qs, admin_obj
    )

    # ── 5b. Filtre spatial zone (buffer 2 km autour du centroïde) ───────
    # L'import OSM se fait par bbox autour du centroïde, ce qui fait
    # déborder les routes d'Adjamé sur Cocody et inversement. Le buffer
    # spatial corrige : on n'affiche que ce qui est vraiment dans la zone.
    roads_qs, floods_qs, veg_qs = _filter_querysets_by_zone_spatial(
        roads_qs, floods_qs, veg_qs, selected_zone
    )

    # ── 5c. Filtre routes stratégiques (default) ────────────────────────
    roads_qs = _apply_strategic_filter(roads_qs, request)

    # NOTE : les géométries (map_data) ne sont plus injectées inline pour éviter
    # une page HTML de plusieurs Mo. Le frontend les charge via /api/map-data/
    # au DOMContentLoaded. Voir dashboard.js → _loadMapData().

    avg_val   = roads_qs.aggregate(avg=Avg("condition_score"))["avg"] or 0
    avg_score = round(float(avg_val), 1)

    center_lat = _js_num(selected_zone.lat_center if selected_zone else 5.35)
    center_lng = _js_num(selected_zone.lng_center if selected_zone else -4.00)

    # Recentrage/cadrage sur l'entité admin sélectionnée (commune, sous-préf,
    # district…) : sans ça, le clic filtre les données mais laisse la carte au
    # cadrage par défaut → effet "données éparpillées / mauvaise zone".
    admin_bounds = None
    if admin_obj is not None and getattr(admin_obj, "geom", None) is not None:
        xmin, ymin, xmax, ymax = admin_obj.geom.extent
        admin_bounds = [[ymin, xmin], [ymax, xmax]]    # [[S,W],[N,E]] pour Leaflet
        _c = admin_obj.geom.centroid
        center_lat, center_lng = _js_num(_c.y), _js_num(_c.x)

    alerts = Alert.objects.filter(
        zone=selected_zone, is_read=False
    ).order_by("-created_at")[:20] if selected_zone else \
             Alert.objects.filter(is_read=False).order_by("-created_at")[:20]

    unread = Alert.objects.filter(is_read=False).count()

    # Histogramme + répartition calculés EN BASE (agrégation conditionnelle)
    # plutôt qu'en chargeant tous les objets en Python.
    from django.db.models import Count, Q
    rb = roads_qs.aggregate(
        b1=Count("pk", filter=Q(condition_score__lte=25)),
        b2=Count("pk", filter=Q(condition_score__gt=25, condition_score__lte=50)),
        b3=Count("pk", filter=Q(condition_score__gt=50, condition_score__lte=75)),
        b4=Count("pk", filter=Q(condition_score__gt=75)),
    )
    chart_routes = {
        "labels": ["0-25", "26-50", "51-75", "76-100"],
        "data":   [rb["b1"], rb["b2"], rb["b3"], rb["b4"]],
    }

    fl = floods_qs.aggregate(
        faible=Count("pk", filter=Q(risk_level="faible")),
        modere=Count("pk", filter=Q(risk_level="modere")),
        eleve=Count("pk", filter=Q(risk_level="eleve")),
        critique=Count("pk", filter=Q(risk_level="critique")),
    )
    chart_floods = {
        "labels": ["Faible", "Modéré", "Élevé", "Critique"],
        "data":   [fl["faible"], fl["modere"], fl["eleve"], fl["critique"]],
    }

    total_roads    = roads_qs.count()
    critical_roads = roads_qs.filter(condition_score__lt=40).count()
    road_health_pct = round(float(avg_score))

    total_floods    = floods_qs.count()
    critical_floods = floods_qs.filter(risk_level__in=["eleve", "critique"]).count()
    avg_flood_val   = floods_qs.aggregate(avg=Avg("risk_score"))["avg"] or 0
    avg_flood_risk  = round(float(avg_flood_val))

    total_veg  = veg_qs.count()
    dense_veg  = veg_qs.filter(density_class__in=["dense", "very_dense"]).count()
    avg_ndvi_v = veg_qs.aggregate(avg=Avg("ndvi_value"))["avg"] or 0
    avg_ndvi   = round(float(avg_ndvi_v), 3)

    # ── Sélecteur admin unifié ──────────────────────────────────────────
    # On annote chaque entité avec sa `select_value` ("level:code") pour que
    # le template puisse comparer simplement contre `admin_filter_value`.
    from admin_divisions.models import District, Region
    districts = list(District.objects.all().order_by("-is_autonomous", "name"))
    for d in districts:
        d.select_value = f"district:{d.code}"

    regions = list(
        Region.objects.select_related("district").all()
        .order_by("district__name", "name")
    )
    for r in regions:
        r.select_value = f"region:{r.code}"

    admin_filter_value = f"{admin_level}:{admin_obj.code}" if admin_obj else ""
    admin_is_autonomous = bool(admin_obj and getattr(admin_obj, "is_autonomous", False))

    # Surface en km² de l'admin sélectionnée (via ST_Area géodésique PostGIS).
    # Pour la vue globale, on garde None — affiché "—" côté template.
    admin_surface_km2 = None
    if admin_obj and admin_obj.geom is not None:
        from django.contrib.gis.db.models.functions import Area
        from django.contrib.gis.measure import D
        try:
            # Cast en geography pour avoir des mètres, puis divisé par 1e6 pour km²
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT ST_Area(%s::geography) / 1000000.0",
                    [admin_obj.geom.ewkt],
                )
                admin_surface_km2 = round(cur.fetchone()[0], 0)
        except Exception:
            admin_surface_km2 = None

    # Panneau gauche : entités groupées par CATÉGORIE de type (Villes,
    # Communes, Districts, Régions, Départements, Sous-préfectures).
    # Réorganisation 2026-07-15 : seuls les niveaux ALIMENTÉS forment des
    # groupes ; les niveaux vides sont résumés en une note discrète
    # (l'architecture 6 niveaux reste prête à les accueillir).
    admin_categories = _build_admin_categories(admin_filter_value)
    admin_categories_filled = [c for c in admin_categories if c["count"]]
    admin_levels_pending = " · ".join(
        c["label"] for c in admin_categories if not c["count"]
    )
    total_zones_count = Zone.objects.count()

    # Markers points (lat/lng) pour la couche commune cliquable de la carte.
    # Volontairement minimal — le détail est fetché à la demande via
    # /api/zones/<code>/stats/ au clic du marker.
    zones_markers = [
        {"code": z.code, "name": z.name, "lat": z.lat_center, "lng": z.lng_center}
        for z in zones
    ]

    # ── Susceptibilité inondation (app flood) ───────────────────────────
    from admin_divisions.models import Commune as _Commune
    from flood.models import CommuneFloodSusceptibility
    flood_rows = list(
        CommuneFloodSusceptibility.objects.select_related("commune")
        .order_by("-susceptibility")
    )
    flood_avg = (
        round(sum(r.susceptibility for r in flood_rows) / len(flood_rows), 1)
        if flood_rows else None
    )
    flood_risk_count = sum(1 for r in flood_rows if r.level in ("eleve", "critique"))
    flood_profile = None
    if admin_level == "commune" and admin_obj is not None:
        flood_profile = next(
            (r for r in flood_rows if r.commune_id == admin_obj.id), None
        )
    communes_count = _Commune.objects.count()

    context = {
        "zones":               zones,
        "selected_zone":       selected_zone,
        "zone_code":           zone_code,
        "flood_rows":          flood_rows,
        "flood_avg":           flood_avg,
        "flood_risk_count":    flood_risk_count,
        "flood_profile":       flood_profile,
        "communes_count":      communes_count,
        "districts":           districts,
        "regions":             regions,
        "admin_categories":    admin_categories_filled,
        "admin_levels_pending": admin_levels_pending,
        "total_zones_count":   total_zones_count,
        "admin_filter_value":  admin_filter_value,
        "admin_label":         admin_label,
        "admin_is_autonomous": admin_is_autonomous,
        "admin_surface_km2":   admin_surface_km2,
        "zones_count":         zones.count(),
        "zones_markers_json":  zones_markers,
       "chart_routes_json": chart_routes,
       "chart_floods_json": chart_floods,
       "avg_score_json":    avg_score,
       "center_lat_json":   center_lat,
        "center_lng_json":   center_lng,
        "admin_bounds_json": admin_bounds,
        "avg_road_score":  avg_score,
        "total_roads":     total_roads,
        "critical_roads":  critical_roads,
        "road_health_pct": road_health_pct,
        "total_floods":    total_floods,
        "critical_floods": critical_floods,
        "avg_flood_risk":  avg_flood_risk,
        "total_veg":       total_veg,
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
    """
    Renvoie l'intégralité des géométries (routes / inondations / végétation)
    consommées par le frontend Leaflet. Structure stable utilisée par
    dashboard.js → _loadMapData() depuis le découplage de la home (point 3).

    GET /api/map-data/?zone=<code>   (paramètre zone optionnel)
    """
    zone_code     = request.GET.get("zone", "")
    selected_zone = Zone.objects.filter(code=zone_code).first() if zone_code else None

    _admin_level, _admin_obj = _parse_admin_filter(request)
    _admin_has_geom = _admin_obj is not None and getattr(_admin_obj, "geom", None) is not None

    if _admin_has_geom:
        # Filtre admin AUTORITAIRE et EXACT : on repart de TOUTES les données et
        # on garde celles dont le point représentatif tombe dans le polygone de
        # l'entité. On IGNORE le tag `zone` (basé sur une bbox d'import OSM, donc
        # imprécis et source de "mélange" entre zones voisines).
        roads_qs  = RoadSegment.objects.all()
        floods_qs = FloodRisk.objects.all()
        veg_qs    = VegetationDensity.objects.all()
        roads_qs, floods_qs, veg_qs = _filter_querysets_by_admin(
            roads_qs, floods_qs, veg_qs, _admin_obj
        )
    else:
        # Fallback legacy : filtre par tag `zone` (import OSM par bbox) si fourni.
        roads_qs  = RoadSegment.objects.filter(zone=selected_zone) if selected_zone \
                    else RoadSegment.objects.all()
        floods_qs = FloodRisk.objects.filter(zone=selected_zone) if selected_zone \
                    else FloodRisk.objects.all()
        veg_qs    = VegetationDensity.objects.filter(zone=selected_zone) if selected_zone \
                    else VegetationDensity.objects.all()

    # Allègement de la carte : par défaut, seuls les axes structurants
    # (motorway/trunk/primary/secondary) sont rendus — réseau décisionnel
    # lisible. ?focus=all = réseau complet (toutes les voies).
    roads_qs = _apply_strategic_filter(roads_qs, request)

    flood_colors = {
        "faible": "#22d3ee", "modere": "#3b82f6",
        "eleve": "#f97316", "critique": "#dc2626",
    }

    return JsonResponse({
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
                "color":       flood_colors.get(f.risk_level, "#3b82f6"),
                "geojson":     _geojson(f),
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
    """
    Export des alertes actives en CSV.
    Paramètres GET :
      - zone : code zone (optionnel)
      - fmt  : format (csv uniquement pour l'instant)
    """
    zone_code = request.GET.get("zone", "")
    qs = Alert.objects.filter(is_read=False).order_by("-created_at")
    if zone_code:
        qs = qs.filter(zone__code=zone_code)

    filename = f"alertes_{zone_code or 'toutes'}_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # BOM UTF-8 pour Excel
    response.write("\ufeff")

    writer = csv.writer(response, delimiter=";")
    writer.writerow([
        "ID", "Titre", "Message", "Severite", "Categorie",
        "Zone", "Latitude", "Longitude", "Date creation"
    ])

    for a in qs:
        writer.writerow([
            a.id,
            a.title,
            a.message,
            a.get_severity_display(),
            a.get_category_display(),
            a.zone.name if a.zone else "",
            _js_num(a.lat),
            _js_num(a.lng),
            a.created_at.strftime("%d/%m/%Y %H:%M"),
        ])

    logger.info("Export CSV alertes — %d lignes (zone: %s)", qs.count(), zone_code or "toutes")
    return response


# ── API — Export routes GeoJSON ────────────────────────────────────────────────

@require_GET
def api_roads_export(request):
    """
    Export des segments routiers en GeoJSON FeatureCollection.
    Paramètres GET :
      - zone    : code zone (optionnel)
      - admin   : <level>:<code> — restreint au polygone admin (optionnel)
      - surface : liste CSV de types de surface (bitume,terre,…) (optionnel)
      - fmt     : format (geojson uniquement pour l'instant)
    """
    zone_code     = request.GET.get("zone", "")
    selected_zone = Zone.objects.filter(code=zone_code).first() if zone_code else None

    qs = RoadSegment.objects.filter(zone=selected_zone) if selected_zone \
         else RoadSegment.objects.all()

    # Filtre admin spatial (même sémantique que l'inventaire).
    _lvl, admin_obj = _parse_admin_filter(request)
    if admin_obj is not None and getattr(admin_obj, "geom", None) is not None:
        qs = qs.filter(geom__isnull=False, geom__intersects=admin_obj.geom)

    # Filtre par type(s) de surface — alimente l'inventaire des routes.
    surfaces = [t.strip() for t in (request.GET.get("surface") or "").split(",") if t.strip()]
    if surfaces:
        qs = qs.filter(surface_type__in=surfaces)

    features = []
    for r in qs:
        geo = _geojson(r)
        if not geo:
            continue
        features.append({
            "type": "Feature",
            "geometry": geo,
            "properties": {
                "id":              r.id,
                "name":            r.name,
                "status":          r.status,
                "status_label":    r.get_status_display(),
                "condition_score": r.condition_score,
                "surface_type":    r.surface_type,
                "surface_label":   r.get_surface_type_display(),
                "is_strategic":    r.is_strategic,
                "notes":           r.notes,
                "zone":            r.zone.name if r.zone else "",
                "zone_code":       r.zone.code if r.zone else "",
                "last_analyzed":   r.last_analyzed.isoformat() if r.last_analyzed else "",
            },
        })

    geojson_data = {
        "type":      "FeatureCollection",
        "name":      f"routes_{zone_code or 'toutes'}",
        "generated": timezone.now().isoformat(),
        "features":  features,
    }

    filename = f"routes_{zone_code or 'toutes'}_{timezone.now().strftime('%Y%m%d_%H%M')}.geojson"
    response = HttpResponse(
        json.dumps(geojson_data, ensure_ascii=False, indent=2),
        content_type="application/geo+json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    logger.info("Export GeoJSON routes — %d features (zone: %s)", len(features), zone_code or "toutes")
    return response


@require_GET
def api_roads_inventory(request):
    """
    Inventaire du réseau routier : agrégats par type de surface (nombre de
    segments + kilométrage réel PostGIS), pour le panneau « Routes ».

    GET /api/roads/inventory/?admin=<level>:<code>   (optionnel)

    Renvoie {"total_count", "total_km", "types": [{code, label, count, km}]}.
    Tous les types du référentiel sont présents (count 0 si vide) + les
    éventuelles classifications hors référentiel trouvées dans les données —
    l'interface reflète TOUJOURS ce que la base contient réellement.
    """
    from django.contrib.gis.db.models.functions import Length
    from django.db.models import Count, Sum

    qs = RoadSegment.objects.filter(geom__isnull=False)
    _lvl, admin_obj = _parse_admin_filter(request)
    if admin_obj is not None and getattr(admin_obj, "geom", None) is not None:
        qs = qs.filter(geom__intersects=admin_obj.geom)

    rows = qs.values("surface_type").annotate(
        n=Count("id"),
        meters=Sum(Length("geom", spheroid=True)),   # mètres géodésiques
    )
    by_type = {r["surface_type"]: r for r in rows}

    def _km(r):
        m = r.get("meters") if r else None
        # Length renvoie un Distance ou un float selon le backend — on
        # normalise en km.
        if m is None:
            return 0.0
        return round((m.m if hasattr(m, "m") else float(m)) / 1000, 1)

    known = dict(RoadSegment.SURFACE_CHOICES)
    types = []
    for code, label in RoadSegment.SURFACE_CHOICES:
        r = by_type.pop(code, None)
        types.append({
            "code":  code,
            "label": label,
            "count": r["n"] if r else 0,
            "km":    _km(r),
        })
    for code, r in sorted(by_type.items()):          # classifications inconnues
        types.append({
            "code":  code or "inconnu",
            "label": code or "Non classé",
            "count": r["n"],
            "km":    _km(r),
        })

    return JsonResponse({
        "total_count": sum(t["count"] for t in types),
        "total_km":    round(sum(t["km"] for t in types), 1),
        "types":       types,
        "scope":       admin_obj.name if admin_obj else "Réseau complet",
    })


# ── API — Stats zone ───────────────────────────────────────────────────────────

@require_GET
def api_zone_stats(request, zone_code):
    """
    Snapshot complet d'une zone/commune pour le popover Leaflet (L4).
    Renvoie nom, description, coordonnées, KPIs routes/inondations/végétation
    + compteur d'alertes actives.
    """
    zone   = get_object_or_404(Zone, code=zone_code)
    roads  = RoadSegment.objects.filter(zone=zone)
    floods = FloodRisk.objects.filter(zone=zone)
    veg    = VegetationDensity.objects.filter(zone=zone)

    avg_score = float(roads.aggregate(avg=Avg("condition_score"))["avg"] or 0)
    avg_flood = float(floods.aggregate(avg=Avg("risk_score"))["avg"] or 0)
    avg_ndvi  = float(veg.aggregate(avg=Avg("ndvi_value"))["avg"] or 0)

    return JsonResponse({
        "code":         zone.code,
        "name":         zone.name,
        "description":  zone.description or "",
        "lat":          zone.lat_center,
        "lng":          zone.lng_center,
        "roads": {
            "total":     roads.count(),
            "avg_score": round(avg_score, 1),
            "critical":  roads.filter(condition_score__lt=40).count(),
        },
        "floods": {
            "total":     floods.count(),
            "avg_score": round(avg_flood, 0),
            "critical":  floods.filter(risk_level__in=["eleve", "critique"]).count(),
        },
        "vegetation": {
            "total":    veg.count(),
            "avg_ndvi": round(avg_ndvi, 3),
            "dense":    veg.filter(density_class__in=["dense", "very_dense"]).count(),
        },
        "alerts_count": Alert.objects.filter(zone=zone, is_read=False).count(),
    })


# ── API — Google Earth Engine ──────────────────────────────────────────────────

from .gee_integration import (
    get_contour_vectors,
    get_flood_extent,
    get_gee_basemap,
    get_ndvi_stats,
    get_point_elevation,
    get_road_surface_index,
)


@require_GET
def api_gee_basemap(request):
    """
    Fond de carte satellite GEE (imagerie Sentinel-2, composite 12 mois).

    GET /api/gee/basemap/
    Renvoie {"imagery_tiles_url"} — fond global non clippé, tuiles
    calculées à la demande par GEE.
    """
    try:
        data = get_gee_basemap()
    except Exception as exc:
        logger.error("[GEE Basemap] Erreur inattendue : %s", exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({"no_data": True}, status=200)
    return JsonResponse(data)


@require_GET
def api_gee_elevation(request):
    """
    Sonde altimétrique : altitude + HAND au point cliqué.

    GET /api/gee/elevation/?lat=<...>&lng=<...>
    """
    try:
        lat = round(float(request.GET.get("lat")), 4)   # ~11 m — clé de cache
        lng = round(float(request.GET.get("lng")), 4)
    except (TypeError, ValueError):
        return JsonResponse({"error": "lat et lng (décimaux) requis"}, status=400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return JsonResponse({"error": "coordonnées hors bornes"}, status=400)

    data = get_point_elevation(lat, lng)
    if data is None:
        return JsonResponse({"no_data": True}, status=200)
    data.update({"lat": lat, "lng": lng})
    return JsonResponse(data)


@require_GET
def api_gee_contours(request):
    """
    Courbes de niveau VECTORIELLES (cotes altimétriques incluses) pour
    l'emprise visible de la carte.

    NB : NON EXPOSÉ dans l'UI depuis le 2026-07-14 (décision produit —
    affichage réservé aux données validées terrain). Les données ont été
    contre-expertisées le même jour : GLO-30 ≈ SRTM ≈ NASADEM ≈ FABDEM
    (écarts < 7 m) et aéroport FHB mesuré à 6 m contre 6,4 m officiels.
    Le service reste disponible pour une réexposition future.

    GET /api/gee/contours/?bbox=<w>,<s>,<e>,<n>&interval=<m>
        bbox     : emprise visible (degrés WGS84), obligatoire
        interval : équidistance en mètres (défaut 10, bornes 2-100)

    Renvoie un FeatureCollection de LineString {"elev", "major"}.
    L'emprise est limitée (~0.8°) : au-delà, le frontend doit zoomer —
    comme sur toute carte topographique, les courbes sont un détail local.
    """
    import math as _math

    raw = (request.GET.get("bbox") or "").split(",")
    try:
        w, s, e, n = (float(p) for p in raw)
    except (TypeError, ValueError):
        return JsonResponse({"error": "bbox=w,s,e,n requis"}, status=400)
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        return JsonResponse({"error": "bbox invalide"}, status=400)
    if (e - w) > 0.8 or (n - s) > 0.8:
        return JsonResponse({"no_data": True, "too_large": True})

    try:
        interval = max(2, min(100, int(request.GET.get("interval", 10))))
    except (TypeError, ValueError):
        interval = 10

    # Emprise étendue au centième de degré EXTÉRIEUR : deux navigations
    # voisines partagent la même clé de cache (le calcul est coûteux).
    bbox = {
        "west":  _math.floor(w * 100) / 100,
        "south": _math.floor(s * 100) / 100,
        "east":  _math.ceil(e * 100) / 100,
        "north": _math.ceil(n * 100) / 100,
    }

    try:
        data = get_contour_vectors(bbox, interval=interval)
    except Exception as exc:
        logger.error("[GEE Contours] Erreur inattendue : %s", exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({"no_data": True})
    return JsonResponse(data)


# ── Courbes de niveau ArcGIS (proxy sécurisé) ──────────────────────────────
# Couche premium Living Atlas « World Contour » (Esri). Elle EXIGE un jeton
# ArcGIS et CONSOMME DES CRÉDITS à chaque tuile. Le jeton (settings.ARCGIS_TOKEN,
# lu depuis .env) reste STRICTEMENT côté serveur : le navigateur n'appelle que
# ce proxy, jamais elevation.arcgis.com — le secret ne fuite pas dans le réseau
# du client. URL du service surchargeable via .env (ARCGIS_CONTOUR_SERVICE) au
# cas où l'organisation pointe une autre couche de courbes.
_WEBMERC_R = 20037508.342789244   # demi-circonférence Web Mercator (m)


def _xyz_to_3857_bbox(z, x, y):
    """Emprise EPSG:3857 (xmin,ymin,xmax,ymax) d'une tuile XYZ."""
    n = 2 ** z
    tile = 2 * _WEBMERC_R / n
    xmin = -_WEBMERC_R + x * tile
    ymax = _WEBMERC_R - y * tile
    return xmin, ymax - tile, xmin + tile, ymax


@require_GET
def api_arcgis_contours(request, z, x, y):
    """
    Proxy de tuiles pour les courbes de niveau ArcGIS (couche abonnés Esri).

    GET /api/arcgis/contours/<z>/<x>/<y>.png

    503 si ARCGIS_TOKEN n'est pas configuré (le frontend prévient alors
    l'utilisateur) ; 502 si ArcGIS renvoie une erreur (jeton expiré/invalide,
    crédits épuisés — Esri répond alors un JSON en HTTP 200, qu'on filtre).
    """
    token = getattr(settings, "ARCGIS_TOKEN", "") or ""
    if not token:
        return HttpResponse("ARCGIS_TOKEN absent", status=503)

    service = getattr(settings, "ARCGIS_CONTOUR_SERVICE", "") or (
        "https://elevation.arcgis.com/arcgis/rest/services/"
        "WorldElevation/Contour/MapServer/export"
    )
    xmin, ymin, xmax, ymax = _xyz_to_3857_bbox(int(z), int(x), int(y))
    url = service + "?" + urlencode({
        "bbox":        f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR":      3857,
        "imageSR":     3857,
        "size":        "256,256",
        "format":      "png32",
        "transparent": "true",
        "f":           "image",
        "token":       token,
    })
    try:
        with urllib.request.urlopen(url, timeout=15) as up:
            ctype = up.headers.get("Content-Type", "")
            body = up.read()
    except Exception as exc:
        logger.warning("[ArcGIS contours] échec tuile %s/%s/%s : %s", z, x, y, exc)
        return HttpResponse(status=502)

    # Jeton invalide / crédits épuisés → Esri renvoie un JSON (HTTP 200).
    # On le traite comme un échec, pas comme une image cassée.
    if "image" not in ctype:
        logger.warning("[ArcGIS contours] réponse non-image (%s) — jeton ?", ctype)
        return HttpResponse(status=502)

    resp = HttpResponse(body, content_type=ctype)
    resp["Cache-Control"] = "public, max-age=86400"   # courbes statiques → 24 h
    return resp


@require_GET
def api_gee_ndvi(request):
    """
    Statistiques NDVI + tiles Leaflet pour le filtre actif.

    GET /api/gee/ndvi/?admin=<level>:<code>   (recommandé — polygone réel)
        /api/gee/ndvi/?zone=<code>            (legacy — cercle 10 km)

    Le tile renvoyé est CLIPPÉ sur la géométrie demandée → l'overlay ne
    déborde plus de la zone choisie.
    """
    geom_geojson, scope = _resolve_gee_geom(request)

    try:
        data = get_ndvi_stats(geom_geojson)
    except Exception as exc:
        logger.error("[GEE NDVI] Erreur inattendue (%s) : %s", scope, exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({
            "error":     "Aucune image Sentinel-2 disponible pour cette zone.",
            "no_data":   True,
            "scope":     scope,
            "tiles_url": None,
        }, status=200)

    data["scope"] = scope
    return JsonResponse(data)


@require_GET
def api_gee_flood(request):
    """Détection SAR + tiles Leaflet (clippées sur la région réelle)."""
    geom_geojson, scope = _resolve_gee_geom(request)

    try:
        data = get_flood_extent(geom_geojson)
    except Exception as exc:
        logger.error("[GEE Flood] Erreur inattendue (%s) : %s", scope, exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({
            "error":     "Données SAR Sentinel-1 insuffisantes pour cette zone.",
            "no_data":   True,
            "scope":     scope,
            "tiles_url": None,
        }, status=200)

    data["scope"] = scope
    return JsonResponse(data)


@require_GET
def api_gee_road(request):
    """Indice qualité chaussée NDWI (Landsat) sur la région réelle."""
    geom_geojson, scope = _resolve_gee_geom(request)

    try:
        data = get_road_surface_index(geom_geojson)
    except Exception as exc:
        logger.error("[GEE Road] Erreur inattendue (%s) : %s", scope, exc)
        return JsonResponse({"error": f"Erreur GEE : {str(exc)}"}, status=500)

    if data is None:
        return JsonResponse({
            "error":   "Données Landsat insuffisantes pour cette zone.",
            "no_data": True,
            "scope":   scope,
        }, status=200)

    data["scope"] = scope
    return JsonResponse(data)


# ── API — Découpage administratif (admin_divisions) ───────────────────────────

@require_GET
def api_admin_divisions(request):
    """
    Renvoie un GeoJSON FeatureCollection des entités du découpage admin.

    GET /api/admin/divisions/?level=<niveau>
        niveaux : district (défaut), region, departement, sousprefecture,
                  ville, commune, all.

    Chaque feature porte dans `properties` : level, code, name + le parent
    pertinent (is_autonomous pour district, district/ville pour les autres).
    Seules les entités dotées d'une géométrie sont renvoyées (clé pour les
    couches cliquables : on ne dessine que ce qui a un polygone).
    """
    from admin_divisions.models import (
        Commune, Departement, District, Region, SousPrefecture, Ville,
    )

    def _commune_props(o):
        props = {
            "district": o.district.name if o.district_id else None,
            "ville":    o.ville.name if o.ville_id else None,
        }
        # Susceptibilité inondation (app flood) si calculée pour la commune.
        fs = getattr(o, "flood_susceptibility", None)
        props["flood_susceptibility"] = fs.susceptibility if fs else None
        props["flood_level"] = fs.level if fs else None
        return props

    # level → (Model, select_related, fonction de propriétés "parent")
    LEVELS = {
        "district":       (District, (), lambda o: {"is_autonomous": o.is_autonomous}),
        "region":         (Region, ("district",), lambda o: {"district": o.district.name}),
        "departement":    (Departement, ("district",), lambda o: {"district": o.district.name}),
        "sousprefecture": (SousPrefecture, ("departement",), lambda o: {"departement": o.departement.name}),
        "ville":          (Ville, ("district",), lambda o: {"district": o.district.name}),
        "commune":        (Commune, ("district", "ville"), _commune_props),
    }

    raw_level = request.GET.get("level", "district")
    if raw_level == "all":
        wanted = list(LEVELS)
    else:
        wanted = [s.strip() for s in raw_level.split(",") if s.strip()]
        if not wanted or any(l not in LEVELS for l in wanted):
            return JsonResponse(
                {"error": "level invalide. Attendu : " + ", ".join(list(LEVELS) + ["all"])},
                status=400,
            )

    # Emprise visible optionnelle : ?bbox=W,S,E,N (WGS84). Ne renvoie que les
    # entités intersectant la fenêtre — indispensable pour les niveaux fins
    # (sous-préfecture = ~7 Mo en entier) : on ne charge que ce qui est visible.
    bbox_poly = None
    raw_bbox = request.GET.get("bbox", "")
    if raw_bbox:
        try:
            from django.contrib.gis.geos import Polygon as GEOSPolygon
            w, s, e, n = [float(x) for x in raw_bbox.split(",")]
            bbox_poly = GEOSPolygon.from_bbox((w, s, e, n))
            bbox_poly.srid = 4326
        except (ValueError, TypeError):
            return JsonResponse({"error": "bbox attendu : W,S,E,N"}, status=400)

    features = []
    for lv in wanted:
        Model, related, parent_props = LEVELS[lv]
        qs = Model.objects.filter(geom__isnull=False)
        if bbox_poly is not None:
            qs = qs.filter(geom__intersects=bbox_poly)
        if related:
            qs = qs.select_related(*related)
        for o in qs:
            props = {"level": lv, "code": o.code, "name": o.name}
            props.update(parent_props(o))
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": json.loads(o.geom.geojson),
            })

    return JsonResponse({"type": "FeatureCollection", "features": features})


# ── API — Occupation des sols (ESA WorldCover via GEE) ────────────────────────

@require_GET
def api_gee_landuse(request):
    """
    Renvoie la distribution d'occupation des sols pour la zone admin filtrée.

    GET /api/gee/landuse/?admin=<level>:<code>
        /api/gee/landuse/?zone=<code>     (utilise un buffer autour du centroïde)

    Réponse :
        {
          "urban": 31.4, "cropland": 24.8, "forest": 22.1,
          "water": 14.2, "bare": 7.5,
          "source": "ESA WorldCover v200 (2021)",
          "scope": "Lagunes"
        }
    """
    from .gee_integration import get_land_use_breakdown

    # 1. Résoudre la géométrie cible
    geom = None
    scope_label = "Côte d'Ivoire"

    admin_level, admin_obj = _parse_admin_filter(request)
    if admin_obj and admin_obj.geom:
        geom = admin_obj.geom
        scope_label = admin_obj.name

    if not geom:
        zone_code = request.GET.get("zone", "").strip()
        if zone_code:
            zone = Zone.objects.filter(code=zone_code).first()
            if zone:
                # Pas de géom polygone sur Zone (juste lat/lng) → buffer 5 km
                # via PostGIS (cast en geography pour des mètres, buffer puis
                # cast retour en geometry 4326).
                from django.contrib.gis.geos import Point
                from django.db import connection
                point = Point(zone.lng_center, zone.lat_center, srid=4326)
                with connection.cursor() as cur:
                    cur.execute(
                        "SELECT ST_AsGeoJSON(ST_Buffer(%s::geography, 5000)::geometry)",
                        [point.wkt],
                    )
                    row = cur.fetchone()
                if row and row[0]:
                    geom_geojson = row[0]
                    result = get_land_use_breakdown(geom_geojson)
                    if result is None:
                        return JsonResponse(
                            {"error": "GEE indisponible ou aucun résultat.",
                             "no_data": True, "scope": zone.name},
                            status=200,
                        )
                    result["scope"] = zone.name
                    return JsonResponse(result)

    if not geom:
        # Fallback : bbox approximative pays entier (utile pour la vue globale)
        from django.contrib.gis.geos import Polygon as GP
        geom = GP.from_bbox((-8.6, 4.3, -2.5, 10.7))
        geom.srid = 4326

    try:
        geom_geojson = geom.geojson if hasattr(geom, "geojson") else json.dumps(geom)
        result = get_land_use_breakdown(geom_geojson)
    except Exception as exc:
        logger.error("[LandUse] erreur : %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)

    if result is None:
        return JsonResponse(
            {"error": "GEE indisponible ou aucun résultat.", "no_data": True,
             "scope": scope_label},
            status=200,
        )
    result["scope"] = scope_label
    return JsonResponse(result)