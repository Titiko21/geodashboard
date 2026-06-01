"""
Settings spécifiques aux tests pytest.

Hérite de config.settings et désactive l'authentification Keycloak (OIDC) :
on teste les vues métier, pas l'intégration SSO. Les tests utilisent
`force_login` (cf. fixture `auth_client` dans dashboard/tests/conftest.py).

Activé via pyproject.toml :
    [tool.pytest.ini_options]
    DJANGO_SETTINGS_MODULE = "config.settings_test"
"""
from config.settings import *  # noqa: F401,F403

# Désactive auth Keycloak pour les tests :
#  - SessionRefresh tente un silent renewal du token OIDC à chaque requête →
#    302 vers Keycloak car le token n'existe pas (force_login ne le crée pas).
#  - LoginRequiredMiddleware peut être contourné par force_login mais autant
#    le retirer aussi pour rester dans un environnement minimal.
KEYCLOAK_ENABLED = False

MIDDLEWARE = [
    m for m in MIDDLEWARE  # noqa: F405
    if "mozilla_django_oidc" not in m
    and "dashboard.middleware.LoginRequiredMiddleware" not in m
]

# AUTHENTICATION_BACKENDS peut référencer KeycloakOIDCBackend — on revient au
# ModelBackend standard pour les tests.
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
