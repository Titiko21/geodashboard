-- Création de la base "keycloak" si absente.
-- Exécuté automatiquement par l'image postgres uniquement lors de la
-- première initialisation du volume postgres_data.
SELECT 'CREATE DATABASE keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec

GRANT ALL PRIVILEGES ON DATABASE keycloak TO CURRENT_USER;
