"""
GéoDash — LoginRequiredMiddleware.

Protège l'intégralité du site : tout chemin non listé dans EXEMPT_PREFIXES
exige une session Django authentifiée. Pour les requêtes browser, redirige
vers Keycloak via /oidc/authenticate/. Pour les appels API (Accept JSON ou
XHR), répond 401 sans redirection — comportement attendu par le front JS.
"""
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse


EXEMPT_PREFIXES = (
    "/oidc/",
    "/health/",
    "/static/",
    "/admin/login/",
    "/admin/logout/",
)


def _wants_json(request):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            return self.get_response(request)

        path = request.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return self.get_response(request)

        if _wants_json(request):
            return JsonResponse(
                {"error": "authentication_required"},
                status=401,
            )

        return redirect_to_login(
            request.get_full_path(),
            login_url=settings.LOGIN_URL,
        )
