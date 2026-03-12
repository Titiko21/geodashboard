from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    # Page principale 
    path("",                                 views.dashboard,           name="index"),

    # API — Données géospatiales 
    path("api/map-data/",                    views.api_map_data,        name="api_map_data"),

    #  API — Alertes 
    path("api/alerts/",                      views.api_alerts,          name="api_alerts"),
    path("api/alerts/<int:alert_id>/read/",  views.api_mark_alert_read, name="api_alert_read"),

    # ── API — Stats par zone 
    path("api/zones/<str:zone_code>/stats/", views.api_zone_stats,      name="api_zone_stats"),

    # ── API — Google Earth Engine (asynchrone, appelé par dashboard.js) 
    path("api/gee/ndvi/",                    views.api_gee_ndvi,        name="api_gee_ndvi"),
    path("api/gee/flood/",                   views.api_gee_flood,       name="api_gee_flood"),
    path("api/gee/road/",                    views.api_gee_road,        name="api_gee_road"),
]