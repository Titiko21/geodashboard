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
    'dashboard',
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

# ── Base de données PostgreSQL ────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE':   'django.db.backends.postgresql',
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
    },

    # Logger racine — capture tout ce qui n'est pas géré ailleurs
    'root': {
        'handlers': ['console', 'geodash_file'],
        'level': 'WARNING',
    },
}