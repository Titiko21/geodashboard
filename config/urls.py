from django.conf import settings
from django.contrib import admin
from django.urls import path, include

from dashboard.health import health_check
from importer.admin_views import import_layer_view

urlpatterns = [
    # Gestion des données : import de couches via l'admin (avant admin.site.urls
    # pour ne pas être avalé par son catch-all).
    path('admin/import-couches/', import_layer_view, name='admin_import_layer'),
    path('admin/', admin.site.urls),
    # Health check complet (DB + DNS + GEE + stats), utilisé par le
    # healthcheck Docker et le monitoring. Voir dashboard/health.py.
    path('health/', health_check, name='health_check'),
]

if getattr(settings, 'KEYCLOAK_ENABLED', False):
    # Fournit /oidc/authenticate/, /oidc/callback/, /oidc/logout/
    urlpatterns += [path('oidc/', include('mozilla_django_oidc.urls'))]

urlpatterns += [
    path('', include('dashboard.urls')),
    path('', include('flood.urls')),
]