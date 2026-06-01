#!/bin/bash
set -e

echo "========================================"
echo "[GéoDash] Démarrage — $(date)"
echo "========================================"

# ── 0. Credentials GEE ──
if [ -n "$GEE_KEY_BASE64" ]; then
  echo "[GéoDash] Décodage credentials GEE..."
  echo "$GEE_KEY_BASE64" | base64 -d > /app/gee_credentials.json
fi

# ── 1. Attendre PostgreSQL ──
echo "[GéoDash] Attente de PostgreSQL..."
DB_HOST="${POSTGRES_HOST:-db}"
DB_USER="${POSTGRES_USER:-geodash}"
DB_NAME="${POSTGRES_DB:-geodash}"

for i in $(seq 1 30); do
  if PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1" > /dev/null 2>&1; then
    echo "[GéoDash] PostgreSQL prêt."
    break
  fi
  if [ "$i" = "30" ]; then
    echo "[GéoDash] ERREUR : PostgreSQL non disponible après 60s"
    exit 1
  fi
  echo "[GéoDash] Tentative $i/30..."
  sleep 2
done

# ── 2. Migrations Django ──
echo "[GéoDash] Migrations Django..."
python manage.py migrate --noinput 2>&1 || true

# ── 3. Import conditionnel (UNE SEULE FOIS) ──
ZONE_COUNT=$(PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -t -c \
  "SELECT COUNT(*) FROM dashboard_zone;" 2>/dev/null | tr -d ' ' || echo "0")

if [ "$ZONE_COUNT" = "0" ] || [ -z "$ZONE_COUNT" ]; then
  if [ -f /app/geodash_dump.sql ]; then
    echo "========================================"
    echo "[GéoDash] Base vide — IMPORT EN COURS..."
    echo "========================================"
    PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" < /app/geodash_dump.sql 2>&1

    NEW_COUNT=$(PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -t -c \
      "SELECT COUNT(*) FROM dashboard_zone;" 2>/dev/null | tr -d ' ')
    echo "[GéoDash] Import terminé — $NEW_COUNT zones en base."
    echo "========================================"
  else
    echo "[GéoDash] ATTENTION : Base vide et pas de dump trouvé."
  fi
else
  echo "[GéoDash] Base déjà peuplée ($ZONE_COUNT zones) — pas d'import."
fi

# ── 3.4 Fix encodage (idempotent via marqueur SQL) ──
if [ -f /app/fix_encoding.sql ]; then
  echo "[GéoDash] Application du fix encodage (idempotent)..."
  PGCLIENTENCODING=UTF8 PGPASSWORD="$POSTGRES_PASSWORD" \
    psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" \
         -v ON_ERROR_STOP=1 -f /app/fix_encoding.sql \
    && echo "[GéoDash] Fix encodage OK." \
    || echo "[GéoDash] ATTENTION : le fix encodage a échoué (voir log ci-dessus)."
else
  echo "[GéoDash] Pas de fix_encoding.sql trouvé — skip."
fi

# ── 3.5 Création superuser (idempotente) ──
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "[GéoDash] Vérification superuser..."
  EXISTS=$(python manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(username='$DJANGO_SUPERUSER_USERNAME').exists())" 2>/dev/null | tail -1)
  if [ "$EXISTS" = "True" ]; then
    echo "[GéoDash] Superuser '$DJANGO_SUPERUSER_USERNAME' déjà présent."
  else
    echo "[GéoDash] Création superuser '$DJANGO_SUPERUSER_USERNAME'..."
    python manage.py createsuperuser --noinput \
      --username "$DJANGO_SUPERUSER_USERNAME" \
      --email "${DJANGO_SUPERUSER_EMAIL:-admin@geodash.local}" 2>&1 || echo "[GéoDash] Création superuser échouée."
  fi
fi

# ── DEBUG temporaire : ce que l'env du conteneur contient ──
echo "[DEBUG ENV] GEE_SERVICE_ACCOUNT='${GEE_SERVICE_ACCOUNT}'"
echo "[DEBUG ENV] GEE_KEY_FILE='${GEE_KEY_FILE}'"
echo "[DEBUG ENV] GEE_PROJECT='${GEE_PROJECT}'"
echo "[DEBUG ENV] GEE_KEY_BASE64 length=${#GEE_KEY_BASE64}"
echo "[DEBUG ENV] /app/gee_credentials.json exists: $([ -f /app/gee_credentials.json ] && echo YES || echo NO)"
echo "[DEBUG ENV] /app/gee_credentials.json size: $(stat -c%s /app/gee_credentials.json 2>/dev/null || echo N/A)"

# ── DEBUG Python : ce que Django voit ──
python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.conf import settings
print(f'[DEBUG DJANGO] GEE_SERVICE_ACCOUNT={settings.GEE_SERVICE_ACCOUNT!r}')
print(f'[DEBUG DJANGO] GEE_KEY_FILE={settings.GEE_KEY_FILE!r}')
print(f'[DEBUG DJANGO] GEE_PROJECT={settings.GEE_PROJECT!r}')
print(f'[DEBUG DJANGO] key_file_exists={os.path.isfile(settings.GEE_KEY_FILE) if settings.GEE_KEY_FILE else False}')
"

# ── 4. Lancer Gunicorn ──
echo "[GéoDash] Démarrage Gunicorn..."
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 3 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -