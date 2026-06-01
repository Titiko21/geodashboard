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

La première fois, l'entrypoint importe `geodash_dump.sql` si la base est
vide (170 zones, ~32k segments, ~3.7k inondations, ~3.7k végétation).

Commandes utiles :

    # Logs en direct
    docker compose logs -f web

    # Console Django dans le conteneur
    docker compose exec web python manage.py shell

    # Activer le scheduler (refresh OSM hebdo + GEE quotidien)
    docker compose --profile scheduler up -d

    # Rafraîchir les scores satellite à la demande
    docker compose exec web python manage.py update_gee_scores

    # Réimporter OSM pour une ville
    docker compose exec web python manage.py populate_geodata --zone TAB

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STRUCTURE DU PROJET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

geodashboard/
├── manage.py                            ← Point d'entrée Django
├── requirements.txt                     ← Dépendances Python (Django, PostGIS, GEE, APScheduler...)
├── docker-compose.yml                   ← Stack : db (postgis), web, scheduler (opt-in), keycloak (opt-in)
├── docker-compose.override.yml          ← Override dev : bind mount + runserver
├── Dockerfile                           ← Image web (Django + GDAL/GEOS/PROJ)
├── entrypoint.sh                        ← Bootstrap conteneur (migrate + import dump si vide)
├── geodash_dump.sql                     ← Dump initial (importé une fois si la DB est vide)
├── docker/
│   ├── postgres-init/                   ← Scripts d'init DB (PostGIS + DB Keycloak)
│   └── keycloak/                        ← Import realm Keycloak
│
├── config/
│   ├── settings.py                      ← Configuration Django (GIS, GEE, Keycloak, logging)
│   └── urls.py                          ← Routes racine
│
└── dashboard/
    ├── models.py                        ← Zone, RoadSegment, FloodRisk, VegetationDensity, Alert
    ├── views.py                         ← Vue dashboard + API REST
    ├── urls.py                          ← Routes API
    ├── admin.py                         ← Interface d'administration
    ├── auth.py / middleware.py          ← Auth OIDC Keycloak (optionnel)
    ├── health.py                        ← Health check Docker (/health/)
    ├── gee_integration.py               ← Google Earth Engine (Sentinel-1/2, Landsat)
    ├── traffic_estimator.py             ← Estimation trafic (OSM + VIIRS)
    ├── templates/dashboard/index.html   ← Interface principale
    └── management/commands/
        ├── populate_geodata.py          ← Import OSM via Overpass
        ├── update_gee_scores.py         ← Calcul scores satellite par segment
        ├── check_missing.py             ← Diagnostic zones sans données
        └── run_scheduler.py             ← Scheduler APScheduler (jobs hebdo/quotidiens)

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
 INTÉGRER VOS DONNÉES RÉELLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Créez une commande de synchronisation dans :
  dashboard/management/commands/sync_from_api.py

Exemple minimal :

    from dashboard.models import RoadSegment, Zone
    import requests

    def sync():
        data = requests.get('https://votre-api.com/routes').json()
        for item in data:
            RoadSegment.objects.update_or_create(
                id=item['id'],
                defaults={
                    'name':            item['nom'],
                    'status':          item['statut'],       # 'bon','degrade','critique','ferme'
                    'condition_score': item['score'],        # 0-100
                    'geojson': {                             # GeoJSON LineString
                        'type': 'LineString',
                        'coordinates': item['coordonnees']   # [[lng,lat], ...]
                    },
                    'zone': Zone.objects.get(code=item['zone_code']),
                }
            )

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
