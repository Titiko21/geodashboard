from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    # Page principale
    path("",                                    views.dashboard,          name="index"),

    # API — Données géospatiales
    path("api/map-data/",                       views.api_map_data,       name="api_map_data"),

    # API — Alertes
    path("api/alerts/",                         views.api_alerts,         name="api_alerts"),
    path("api/alerts/<int:alert_id>/read/",     views.api_mark_alert_read, name="api_alert_read"),
    path("api/alerts/export/",                  views.api_alerts_export,  name="api_alerts_export"),

    # API — Export routes GeoJSON
    path("api/roads/export/",                   views.api_roads_export,   name="api_roads_export"),

    # API — Stats par zone
    path("api/zones/<str:zone_code>/stats/",    views.api_zone_stats,     name="api_zone_stats"),

    # API — Google Earth Engine
    path("api/gee/ndvi/",                       views.api_gee_ndvi,       name="api_gee_ndvi"),
    path("api/gee/flood/",                      views.api_gee_flood,      name="api_gee_flood"),
    path("api/gee/road/",                       views.api_gee_road,       name="api_gee_road"),
    path("api/gee/landuse/",                    views.api_gee_landuse,    name="api_gee_landuse"),
    path("api/gee/contours/",                   views.api_gee_contours,   name="api_gee_contours"),
    path("api/gee/elevation/",                  views.api_gee_elevation,  name="api_gee_elevation"),

    # API — Découpage administratif (Phase B)
    path("api/admin/divisions/",                views.api_admin_divisions, name="api_admin_divisions"),
]