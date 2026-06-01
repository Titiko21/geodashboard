-- Active l'extension PostGIS sur la DB principale GéoDash.
-- Exécuté UNIQUEMENT lors de la première initialisation du volume postgres_data
-- (les scripts /docker-entrypoint-initdb.d/ ne sont pas rejoués sur un volume
-- existant). Pour activer PostGIS sur une DB déjà peuplée :
--   docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB \
--     -c "CREATE EXTENSION IF NOT EXISTS postgis;"
--
-- Le préfixe "00-" garantit l'exécution AVANT 01-create-keycloak-db.sql.

CREATE EXTENSION IF NOT EXISTS postgis;
