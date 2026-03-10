# GéoDash — Tableau de Bord Géospatial Django

Interface de décision pour visualiser l'état des routes, les risques d'inondation
et la densité de végétation en temps réel sur une carte interactive.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 DÉMARRAGE RAPIDE (Windows)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Option A — Double-cliquer sur  demarrer.bat  (tout automatique)

Option B — Terminal manuel :

    pip install -r requirements.txt
    python manage.py migrate
    python manage.py populate_sample_data
    python manage.py runserver

Puis ouvrir :  http://127.0.0.1:8000/

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STRUCTURE DU PROJET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

geodashboard/
├── demarrer.bat                         ← Script de démarrage Windows
├── manage.py                            ← Point d'entrée Django
├── requirements.txt                     ← Dépendances (Django uniquement)
│
├── config/
│   ├── settings.py                      ← Configuration Django
│   └── urls.py                          ← Routes principales
│
└── dashboard/
    ├── models.py                        ← Modèles (Zone, Route, Inondation, Végétation, Alerte)
    ├── views.py                         ← Vues + API REST
    ├── urls.py                          ← Routes de l'application
    ├── admin.py                         ← Interface d'administration
    ├── templates/dashboard/index.html  ← Interface principale
    └── management/commands/
        └── populate_sample_data.py     ← Données de démonstration

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
