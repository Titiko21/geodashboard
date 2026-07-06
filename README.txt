# GéoDash — Tableau de Bord Géospatial Django

Interface de décision pour visualiser l'état des routes, les risques d'inondation
et la densité de végétation en temps réel sur une carte interactive.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 DÉMARRAGE RAPIDE (Docker dev local — Windows / Linux / macOS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pré-requis : Docker Desktop installé.

    1. Copier .env.example en .env et renseigner les variables.
    2. docker compose up -d
    3. Ouvrir http://localhost:8000

Au démarrage, l'entrypoint applique les migrations puis importe les
communes du Grand Abidjan (idempotent) — seule source de données actuelle.

Commandes utiles :

    # Logs en direct
    docker compose logs -f web

    # Console Django dans le conteneur
    docker compose exec web python manage.py shell

    # Rafraîchir les scores satellite à la demande (manuel)
    docker compose exec web python manage.py update_gee_scores

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STRUCTURE DU PROJET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

geodashboard/
├── manage.py                            ← Point d'entrée Django
├── requirements.txt                     ← Dépendances Python (Django, PostGIS, GEE...)
├── docker-compose.yml                   ← Stack : db (postgis), web, keycloak (opt-in)
├── docker-compose.override.yml          ← Override dev : bind mount + runserver
├── Dockerfile                           ← Image web (Django + GDAL/GEOS/PROJ)
├── entrypoint.sh                        ← Bootstrap conteneur (migrate + import communes)
├── docker/
│   ├── postgres-init/                   ← Scripts d'init DB (PostGIS + DB Keycloak)
│   └── keycloak/                        ← Import realm Keycloak
│
├── config/
│   ├── settings.py                      ← Configuration Django (GIS, GEE, Keycloak, logging)
│   └── urls.py                          ← Routes racine
│
├── importer/                            ← Importeur générique multi-format
│   ├── engine.py                        ← Moteur : OGR → mapping → upsert idempotent
│   ├── registry.py                      ← Cibles d'import (Phase A : commune)
│   ├── mappings/                        ← Fichiers de mapping JSON livrés
│   └── management/commands/
│       └── import_layer.py              ← CLI : --file --mapping [--dry-run|--list-layers]
│
└── dashboard/
    ├── models.py                        ← Zone, RoadSegment, FloodRisk, VegetationDensity, Alert
    ├── views.py                         ← Vue dashboard + API REST
    ├── urls.py                          ← Routes API
    ├── admin.py                         ← Interface d'administration
    ├── auth.py / middleware.py          ← Auth OIDC Keycloak (optionnel)
    ├── health.py                        ← Health check Docker (/health/)
    ├── gee_integration.py               ← Google Earth Engine (Sentinel-1/2, Landsat)
    ├── _geo_utils.py                    ← Helpers géométriques (GeoJSON → geom, centroïde)
    ├── templates/dashboard/index.html   ← Interface principale
    └── management/commands/
        └── update_gee_scores.py         ← Calcul scores satellite par segment

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FONCTIONNALITÉS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• Carte Leaflet interactive (fond sombre CartoDB)
  - Couche Routes      : polylignes colorées selon l'état (bon/dégradé/critique/fermé)
  - Couche Inondations : polygones colorés selon le niveau de risque
  - Couche Végétation  : polygones NDVI avec variation temporelle
  - Popups détaillés au clic sur chaque objet
  - Zoom automatique sur la zone sélectionnée

• Sidebar avec indicateurs clés (KPIs)
  - Score de santé routière avec barre de progression
  - Score de risque d'inondation
  - Indice NDVI moyen
  - Clic sur un KPI = filtrage automatique de la couche correspondante

• Système d'alertes en temps réel
  - Classées par sévérité : info / warning / danger / critical
  - Clic sur une alerte = zoom vers la zone concernée
  - Rafraîchissement automatique toutes les 60 secondes

• Graphiques en bas de page
  - Distribution des états routiers (barres)
  - Niveaux de risque inondation (donut)
  - Jauge de score global

• Filtrage par zone géographique (menu déroulant en haut)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 API REST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  GET  /api/map-data/?layer=all&zone=ABJ-N   → GeoJSON pour la carte
  GET  /api/alerts/                          → Alertes non lues (JSON)
  POST /api/alerts/<id>/read/               → Marquer une alerte comme lue
  GET  /api/zones/<code>/stats/             → Stats d'une zone

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 IMPORTER VOS DONNÉES (GeoJSON / Shapefile / GeoPackage)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

L'importeur générique (app `importer`) charge n'importe quel fichier
géospatial vers une couche de l'application, sans écrire de code.

IMPORTANT : toutes les commandes manage.py s'exécutent DANS le conteneur
(le Python local n'a ni GDAL ni les dépendances). Déposez d'abord votre
fichier dans le dossier du projet (monté dans le conteneur sous /app),
par exemple dans un sous-dossier `data_import/`.

    # 1. Explorer le fichier (couches, colonnes)
    docker compose exec web python manage.py import_layer \
        --file data_import/mon_fichier.gpkg --list-layers

    # 2. Écrire un mapping JSON (colonne du fichier → entrée de la cible)
    #    Exemple : importer/mappings/communes_grand_abidjan.json
    #    { "target": "commune", "columns": { "name": ["NOMS", "NOM"] } }

    # 3. Prévisualiser (aucune écriture)
    docker compose exec web python manage.py import_layer \
        --file data_import/mon_fichier.geojson --mapping data_import/mon_mapping.json --dry-run

    # 4. Importer (idempotent : rejouable sans doublon)
    docker compose exec web python manage.py import_layer \
        --file data_import/mon_fichier.geojson --mapping data_import/mon_mapping.json

Cibles disponibles (importer/registry.py) : commune.
Les cibles routes (BDR), drainage, terrain s'ajouteront au fil des phases.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ADMINISTRATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Créer un compte admin :
    python manage.py createsuperuser

Accéder à l'admin : http://127.0.0.1:8000/admin/
→ Gérer zones, routes, inondations, végétation et alertes via l'interface web

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AUTHENTIFICATION SSO — KEYCLOAK (OIDC)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Le SSO Keycloak est OPTIONNEL et désactivé par défaut.
Pour l'activer : KEYCLOAK_ENABLED=true dans .env
(sans cette variable, le projet fonctionne comme avant — utile en dev offline).

Variables .env requises quand KEYCLOAK_ENABLED=true :

    KEYCLOAK_ENABLED=true
    KEYCLOAK_URL=http://keycloak:8080            # interne Docker (Django → KC)
    KEYCLOAK_PUBLIC_URL=http://localhost:8080    # browser-facing (KC → user)
    KEYCLOAK_REALM=geodash
    OIDC_RP_CLIENT_ID=geodash-web
    OIDC_RP_CLIENT_SECRET=<client-secret>
    KEYCLOAK_ADMIN=admin
    KEYCLOAK_ADMIN_PASSWORD=<mot-de-passe-fort>

Démarrage avec SSO :

    docker compose --profile sso up -d
    # Sans le profil sso, Keycloak n'est pas démarré → KEYCLOAK_ENABLED doit
    # alors pointer vers une instance externe.

Configuration initiale du realm Keycloak (à faire UNE fois via la console
http://localhost:8080) :

  1. Créer un realm "geodash"
  2. Créer un client "geodash-web" :
       - Client type    : OpenID Connect
       - Client auth    : ON  (confidential)
       - Standard flow  : ON
       - Valid redirect URIs : http://localhost:8000/oidc/callback/
                               https://geodash.exemple.com/oidc/callback/
       - Valid post logout : http://localhost:8000/  + URL prod
       - Web origins    : +
       Récupérer le secret dans l'onglet "Credentials" → OIDC_RP_CLIENT_SECRET
  3. Créer 2 rôles realm : geodash-staff, geodash-admin
  4. Créer un utilisateur de test, lui assigner geodash-admin, définir un
     mot de passe (onglet Credentials).

Routes ajoutées par mozilla-django-oidc :
    /oidc/authenticate/  → redirige vers Keycloak (= "page de login")
    /oidc/callback/      → reçoit le code, crée la session
    /oidc/logout/        → déconnecte Django + Keycloak (single-logout)

Compatibilité :
  • Le superuser local Django reste fonctionnel pour /admin/
    (ModelBackend conservé en second backend).
  • Les API JSON renvoient 401 au lieu de rediriger (cf. middleware).
  • L'endpoint /health/ reste public pour le healthcheck Docker.
