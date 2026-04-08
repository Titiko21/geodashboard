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

# ── 4. Lancer Gunicorn ──
echo "[GéoDash] Démarrage Gunicorn..."
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 3 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -