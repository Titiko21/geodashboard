from django.urls import path
from . import views
from . import gee_views

app_name = 'dashboard'

urlpatterns = [
    # Page principale
    path('', views.dashboard, name='index'),

    # API données carte et alertes
    path('api/map-data/',                    views.api_map_data,          name='api_map_data'),
    path('api/alerts/',                      views.api_alerts,            name='api_alerts'),
    path('api/alerts/<int:alert_id>/read/',  views.api_mark_alert_read,   name='api_mark_alert_read'),
    path('api/zones/<str:zone_code>/stats/', views.api_zone_stats,        name='api_zone_stats'),

    # API Google Earth Engine
    path('api/gee/status/',                  gee_views.api_gee_status,    name='api_gee_status'),
    path('api/gee/sync/',                    gee_views.api_gee_sync,      name='api_gee_sync'),
]
