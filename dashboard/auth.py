"""
GéoDash — Authentification Keycloak (OIDC).

- KeycloakOIDCBackend : extension de OIDCAuthenticationBackend qui synchronise
  les rôles realm Keycloak vers les flags Django (is_staff, is_superuser) et
  rattache l'utilisateur aux Groups Django correspondants.

- keycloak_logout : construit l'URL end_session_endpoint pour terminer la
  session SSO côté Keycloak (single-logout). Référencée par
  OIDC_OP_LOGOUT_URL_METHOD dans settings.py.
"""
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import Group
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


ADMIN_ROLE = "geodash-admin"
STAFF_ROLE = "geodash-staff"


class KeycloakOIDCBackend(OIDCAuthenticationBackend):
    def _extract_roles(self, claims):
        realm = claims.get("realm_access") or {}
        return set(realm.get("roles") or [])

    def _apply_roles(self, user, roles):
        user.is_superuser = ADMIN_ROLE in roles
        user.is_staff = user.is_superuser or STAFF_ROLE in roles
        user.save(update_fields=["is_superuser", "is_staff"])

        kc_groups = {r for r in roles if r.startswith("geodash-")}
        if not kc_groups:
            return
        existing = {g.name for g in user.groups.all()}
        for name in kc_groups - existing:
            group, _ = Group.objects.get_or_create(name=name)
            user.groups.add(group)
        for name in existing & {ADMIN_ROLE, STAFF_ROLE} - kc_groups:
            user.groups.remove(Group.objects.get(name=name))

    def create_user(self, claims):
        user = super().create_user(claims)
        user.username = claims.get("preferred_username") or user.email
        user.first_name = claims.get("given_name", "")
        user.last_name = claims.get("family_name", "")
        user.save()
        self._apply_roles(user, self._extract_roles(claims))
        return user

    def update_user(self, user, claims):
        user.first_name = claims.get("given_name", user.first_name)
        user.last_name = claims.get("family_name", user.last_name)
        user.email = claims.get("email", user.email)
        user.save()
        self._apply_roles(user, self._extract_roles(claims))
        return user


def keycloak_logout(request):
    """
    Construit l'URL de fin de session Keycloak (single-logout).
    Appelée par mozilla_django_oidc lors du POST sur /oidc/logout/.
    """
    id_token = request.session.get("oidc_id_token")
    redirect_uri = request.build_absolute_uri(settings.LOGOUT_REDIRECT_URL or "/")
    params = {"post_logout_redirect_uri": redirect_uri}
    if id_token:
        params["id_token_hint"] = id_token
    else:
        params["client_id"] = settings.OIDC_RP_CLIENT_ID
    return f"{settings.KEYCLOAK_LOGOUT_ENDPOINT_PUBLIC}?{urlencode(params)}"
