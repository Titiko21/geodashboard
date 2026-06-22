from pathlib import Path
import os

# Charge le fichier .env automatiquement
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
except ImportError:
    pass  # python-dotenv pas encore installé, les variables système seront utilisées

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-only-fallback-key-change-in-production')

DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.gis',
    'dashboard',
    'admin_divisions',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
     'whitenoise.middleware.WhiteNoiseMiddleware', 
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Base de données PostgreSQL + PostGIS ──────────────────────────────────────
# Backend GIS requis pour les GeometryField (étape 2.3+). Le backend postgis
# reste 100% compatible avec les requêtes ORM standard sur les modèles non-GIS.
DATABASES = {
    'default': {
        'ENGINE':   'django.contrib.gis.db.backends.postgis',
        'NAME':     os.environ.get('POSTGRES_DB'),
        'USER':     os.environ.get('POSTGRES_USER'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD'),
        'HOST':     os.environ.get('POSTGRES_HOST', 'db'),
        'PORT':     os.environ.get('POSTGRES_PORT', '5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Authentification SSO Keycloak (OIDC) ──────────────────────────────────────
# Activable via KEYCLOAK_ENABLED=true. Désactivé par défaut pour préserver le
# fonctionnement local sans dépendance externe.
#
# En Docker, le navigateur et le conteneur Django ne joignent pas Keycloak par
# la même URL : on sépare donc KEYCLOAK_URL (interne, token/jwks) et
# KEYCLOAK_PUBLIC_URL (browser-facing, authorize/logout).

KEYCLOAK_ENABLED = os.environ.get('KEYCLOAK_ENABLED', 'False').lower() in ('true', '1', 'yes')

if KEYCLOAK_ENABLED:
    KEYCLOAK_URL        = os.environ['KEYCLOAK_URL'].rstrip('/')
    KEYCLOAK_PUBLIC_URL = os.environ.get('KEYCLOAK_PUBLIC_URL', KEYCLOAK_URL).rstrip('/')
    KEYCLOAK_REALM      = os.environ['KEYCLOAK_REALM']

    _kc_internal = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"
    _kc_public   = f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"

    INSTALLED_APPS += ['mozilla_django_oidc']

    AUTHENTICATION_BACKENDS = [
        'dashboard.auth.KeycloakOIDCBackend',
        'django.contrib.auth.backends.ModelBackend',  # superuser local pour /admin/
    ]

    MIDDLEWARE += [
        'mozilla_django_oidc.middleware.SessionRefresh',
        'dashboard.middleware.LoginRequiredMiddleware',
    ]

    OIDC_RP_CLIENT_ID     = os.environ['OIDC_RP_CLIENT_ID']
    OIDC_RP_CLIENT_SECRET = os.environ['OIDC_RP_CLIENT_SECRET']
    OIDC_RP_SIGN_ALGO     = 'RS256'
    OIDC_RP_SCOPES        = 'openid email profile'
    OIDC_USE_PKCE         = True

    # Endpoints navigateur (authorize)
    OIDC_OP_AUTHORIZATION_ENDPOINT = f"{_kc_public}/auth"
    # Endpoints serveur (token, userinfo, jwks) — appelés depuis Django
    OIDC_OP_TOKEN_ENDPOINT    = f"{_kc_internal}/token"
    OIDC_OP_USER_ENDPOINT     = f"{_kc_internal}/userinfo"
    OIDC_OP_JWKS_ENDPOINT     = f"{_kc_internal}/certs"

    # Logout SSO : helper construit l'URL avec id_token_hint
    OIDC_OP_LOGOUT_URL_METHOD     = 'dashboard.auth.keycloak_logout'
    KEYCLOAK_LOGOUT_ENDPOINT_PUBLIC = f"{_kc_public}/logout"
    OIDC_STORE_ID_TOKEN = True
    OIDC_STORE_ACCESS_TOKEN = True

    # Renouvellement silencieux des tokens (mozilla SessionRefresh)
    OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS = 15 * 60

    LOGIN_URL           = '/oidc/authenticate/'
    LOGIN_REDIRECT_URL  = '/'
    LOGOUT_REDIRECT_URL = '/'

    # Hardening cookies / sessions (actif en prod uniquement)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    CSRF_COOKIE_SAMESITE    = 'Lax'
    if not DEBUG:
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE    = True
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ── Google Earth Engine ───────────────────────────────────────────────────────

GEE_SERVICE_ACCOUNT = os.environ.get('GEE_SERVICE_ACCOUNT', '')
GEE_KEY_FILE        = os.environ.get('GEE_KEY_FILE', '')
GEE_PROJECT         = os.environ.get('GEE_PROJECT', '')

# ── Logging ───────────────────────────────────────────────────────────────────
# En développement  : tout s'affiche dans la console, niveau DEBUG.
# En production     : les erreurs sont écrites dans logs/geodash.log,
#                     les warnings et erreurs Django dans logs/django.log.
# Le dossier logs/  est créé automatiquement s'il n'existe pas.

LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,

    # ── Formateurs ──
    'formatters': {
        'verbose': {
            'format': '{asctime} [{levelname}] {name} — {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '[{levelname}] {message}',
            'style': '{',
        },
    },

    # ── Filtres ──
    'filters': {
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
    },

    # ── Handlers ──
    'handlers': {
        # Console — actif uniquement en développement (DEBUG=True)
        'console': {
            'level': 'DEBUG',
            'filters': ['require_debug_true'],
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },

        # Fichier général Django — erreurs en production
        'django_file': {
            'level': 'WARNING',
            'filters': ['require_debug_false'],
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'django.log',
            'maxBytes': 5 * 1024 * 1024,   # 5 MB par fichier
            'backupCount': 3,               # garde les 3 derniers fichiers
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },

        # Fichier applicatif GéoDash — toujours actif (dev + prod)
        'geodash_file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'geodash.log',
            'maxBytes': 10 * 1024 * 1024,  # 10 MB par fichier
            'backupCount': 5,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },

    # ── Loggers ──
    'loggers': {
        # Logger Django interne
        'django': {
            'handlers': ['console', 'django_file'],
            'level': 'INFO',
            'propagate': False,
        },

        # Requêtes HTTP Django (trop verbeux en prod, on les filtre)
        'django.request': {
            'handlers': ['django_file'],
            'level': 'ERROR',
            'propagate': False,
        },

        # Toute l'application dashboard (views, admin, commandes)
        'dashboard': {
            'handlers': ['console', 'geodash_file'],
            'level': 'DEBUG',
            'propagate': False,
        },

        # Diagnostic SSO — rend visibles les échecs de login OIDC en console.
        'mozilla_django_oidc': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },

    # Logger racine — capture tout ce qui n'est pas géré ailleurs
    'root': {
        'handlers': ['console', 'geodash_file'],
        'level': 'WARNING',
    },
}