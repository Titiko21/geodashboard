from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from dashboard.models import Zone, RoadSegment, FloodRisk, VegetationDensity, Alert


class Command(BaseCommand):
    help = 'Charge des données de démonstration pour le tableau de bord géospatial'

    def handle(self, *args, **options):
        self.stdout.write('🗑️  Suppression des données existantes...')
        Alert.objects.all().delete()
        VegetationDensity.objects.all().delete()
        FloodRisk.objects.all().delete()
        RoadSegment.objects.all().delete()
        Zone.objects.all().delete()

        # ── ZONES ──
        self.stdout.write('📍 Création des zones...')
        zones = {}
        zones_data = [
            {'name': 'Abidjan Nord', 'code': 'ABJ-N', 'lat':  5.380, 'lng': -4.020,
             'desc': 'Zone nord de la métropole, incluant Abobo et Adjamé'},
            {'name': 'Abidjan Sud',  'code': 'ABJ-S', 'lat':  5.290, 'lng': -3.990,
             'desc': 'Zone sud, port autonome, Marcory, Vridi'},
            {'name': 'Yamoussoukro','code': 'YAM',   'lat':  6.820, 'lng': -5.280,
             'desc': 'Capitale politique, lac artificiel'},
            {'name': 'Bouaké',      'code': 'BOU',   'lat':  7.690, 'lng': -5.030,
             'desc': 'Deuxième ville du pays, centre commercial'},
        ]
        for zd in zones_data:
            z = Zone.objects.create(
                name=zd['name'], code=zd['code'],
                lat_center=zd['lat'], lng_center=zd['lng'],
                description=zd['desc']
            )
            zones[zd['code']] = z

        # ── ROUTES ──
        self.stdout.write('🛣️  Création des segments routiers...')
        roads_data = [
            # Abidjan Nord
            {
                'zone': 'ABJ-N', 'name': 'Boulevard de la Corniche', 'status': 'bon', 'score': 87,
                'notes': 'Dernière réfection il y a 8 mois. État satisfaisant.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-4.025, 5.375], [-4.018, 5.380], [-4.010, 5.386]
                ]},
            },
            {
                'zone': 'ABJ-N', 'name': 'Autoroute du Nord — Section 1', 'status': 'degrade', 'score': 52,
                'notes': 'Nids-de-poule signalés, revêtement fissuré sur 400m.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-4.030, 5.370], [-4.020, 5.378], [-4.012, 5.388]
                ]},
            },
            {
                'zone': 'ABJ-N', 'name': 'Rue des Jardins', 'status': 'critique', 'score': 23,
                'notes': 'Déformation importante chaussée. Intervention urgente.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-4.019, 5.377], [-4.016, 5.380], [-4.013, 5.383]
                ]},
            },
            {
                'zone': 'ABJ-N', 'name': 'Avenue Jean-Paul II', 'status': 'bon', 'score': 91,
                'notes': 'Très bon état général. Marquage au sol récent.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-4.022, 5.385], [-4.016, 5.388], [-4.011, 5.391]
                ]},
            },
            # Abidjan Sud
            {
                'zone': 'ABJ-S', 'name': 'Boulevard VGE', 'status': 'bon', 'score': 79,
                'notes': 'Bon état, légère usure des bordures.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-3.997, 5.295], [-3.987, 5.292], [-3.977, 5.289]
                ]},
            },
            {
                'zone': 'ABJ-S', 'name': 'Pont HKB', 'status': 'degrade', 'score': 61,
                'notes': 'Revêtement dégradé. Surveillance accrue recommandée.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-3.990, 5.298], [-3.988, 5.292], [-3.986, 5.286]
                ]},
            },
            {
                'zone': 'ABJ-S', 'name': 'Route de Bassam — Section critique', 'status': 'ferme', 'score': 8,
                'notes': 'Fermée suite aux inondations. Accès interdit.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-3.982, 5.283], [-3.971, 5.280], [-3.961, 5.277]
                ]},
            },
            # Yamoussoukro
            {
                'zone': 'YAM', 'name': 'Autoroute A1 — Section Yamoussoukro', 'status': 'bon', 'score': 94,
                'notes': 'Excellente infrastructure. Entretien régulier.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-5.292, 6.815], [-5.281, 6.820], [-5.271, 6.825]
                ]},
            },
            {
                'zone': 'YAM', 'name': 'Route du Lac', 'status': 'degrade', 'score': 44,
                'notes': 'Érosion latérale visible sur 600m. Priorité moyenne.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-5.287, 6.822], [-5.280, 6.818], [-5.273, 6.815]
                ]},
            },
            # Bouaké
            {
                'zone': 'BOU', 'name': 'Boulevard de la Paix', 'status': 'critique', 'score': 31,
                'notes': 'Nids-de-poule profonds. Intervention dans les 72h.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-5.037, 7.685], [-5.029, 7.692], [-5.021, 7.697]
                ]},
            },
            {
                'zone': 'BOU', 'name': 'Route Nationale RN3', 'status': 'bon', 'score': 72,
                'notes': 'État correct. Marquage à rafraîchir.',
                'geojson': {'type': 'LineString', 'coordinates': [
                    [-5.042, 7.693], [-5.031, 7.690], [-5.021, 7.687]
                ]},
            },
        ]
        for rd in roads_data:
            RoadSegment.objects.create(
                zone=zones[rd['zone']], name=rd['name'],
                status=rd['status'], condition_score=rd['score'],
                geojson=rd['geojson'], notes=rd['notes'],
            )

        # ── INONDATIONS ──
        self.stdout.write('🌊 Création des zones d\'inondation...')
        floods_data = [
            {
                'zone': 'ABJ-S', 'name': 'Bas-fond Basse Cocody', 'risk': 'eleve',
                'score': 72, 'area': 3.4, 'rain': 85,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-3.994, 5.291], [-3.984, 5.291],
                    [-3.984, 5.299], [-3.994, 5.299], [-3.994, 5.291]
                ]]},
            },
            {
                'zone': 'ABJ-S', 'name': 'Plaine de Marcory', 'risk': 'critique',
                'score': 88, 'area': 5.1, 'rain': 120,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-3.980, 5.280], [-3.967, 5.280],
                    [-3.967, 5.291], [-3.980, 5.291], [-3.980, 5.280]
                ]]},
            },
            {
                'zone': 'ABJ-N', 'name': 'Vallée d\'Abobo', 'risk': 'modere',
                'score': 45, 'area': 2.2, 'rain': 55,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-4.027, 5.382], [-4.014, 5.382],
                    [-4.014, 5.391], [-4.027, 5.391], [-4.027, 5.382]
                ]]},
            },
            {
                'zone': 'YAM', 'name': 'Rives du Lac de Yamoussoukro', 'risk': 'faible',
                'score': 22, 'area': 8.7, 'rain': 30,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-5.297, 6.814], [-5.271, 6.814],
                    [-5.271, 6.830], [-5.297, 6.830], [-5.297, 6.814]
                ]]},
            },
            {
                'zone': 'BOU', 'name': 'Bas-fond Nord Bouaké', 'risk': 'eleve',
                'score': 65, 'area': 4.3, 'rain': 95,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-5.043, 7.692], [-5.022, 7.692],
                    [-5.022, 7.701], [-5.043, 7.701], [-5.043, 7.692]
                ]]},
            },
        ]
        for fd in floods_data:
            FloodRisk.objects.create(
                zone=zones[fd['zone']], name=fd['name'],
                risk_level=fd['risk'], risk_score=fd['score'],
                area_km2=fd['area'], rainfall_mm=fd['rain'],
                geojson=fd['geojson'],
            )

        # ── VÉGÉTATION ──
        self.stdout.write('🌿 Création des données de végétation...')
        vegs_data = [
            {
                'zone': 'ABJ-N', 'name': 'Forêt du Banco', 'ndvi': 0.72,
                'class': 'very_dense', 'cov': 91, 'chg': +0.02,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-4.029, 5.374], [-4.009, 5.374],
                    [-4.009, 5.393], [-4.029, 5.393], [-4.029, 5.374]
                ]]},
            },
            {
                'zone': 'ABJ-N', 'name': 'Parc du Plateau', 'ndvi': 0.41,
                'class': 'moderate', 'cov': 48, 'chg': -0.03,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-4.023, 5.376], [-4.013, 5.376],
                    [-4.013, 5.384], [-4.023, 5.384], [-4.023, 5.376]
                ]]},
            },
            {
                'zone': 'ABJ-S', 'name': 'Mangroves de Vridi', 'ndvi': 0.58,
                'class': 'dense', 'cov': 74, 'chg': -0.07,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-3.993, 5.281], [-3.979, 5.281],
                    [-3.979, 5.294], [-3.993, 5.294], [-3.993, 5.281]
                ]]},
            },
            {
                'zone': 'YAM', 'name': 'Savane de Yamoussoukro', 'ndvi': 0.29,
                'class': 'sparse', 'cov': 32, 'chg': -0.05,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-5.295, 6.812], [-5.270, 6.812],
                    [-5.270, 6.829], [-5.295, 6.829], [-5.295, 6.812]
                ]]},
            },
            {
                'zone': 'BOU', 'name': 'Forêt-galerie de Bouaké', 'ndvi': 0.63,
                'class': 'dense', 'cov': 81, 'chg': +0.01,
                'geojson': {'type': 'Polygon', 'coordinates': [[
                    [-5.041, 7.686], [-5.021, 7.686],
                    [-5.021, 7.702], [-5.041, 7.702], [-5.041, 7.686]
                ]]},
            },
        ]
        for vd in vegs_data:
            VegetationDensity.objects.create(
                zone=zones[vd['zone']], name=vd['name'],
                ndvi_value=vd['ndvi'], density_class=vd['class'],
                coverage_percent=vd['cov'], change_vs_previous=vd['chg'],
                geojson=vd['geojson'],
            )

        # ── ALERTES ──
        self.stdout.write('🚨 Création des alertes...')
        now = timezone.now()
        alerts_data = [
            {
                'zone': 'ABJ-S', 'sev': 'critical', 'cat': 'road',
                'title': 'Route de Bassam fermée',
                'msg':   'Score de dégradation critique (8/100). Route fermée suite aux inondations récentes.',
                'lat': 5.280, 'lng': -3.971, 'hours_ago': 1,
            },
            {
                'zone': 'ABJ-S', 'sev': 'critical', 'cat': 'flood',
                'title': 'Risque inondation critique — Marcory',
                'msg':   'Précipitations de 120 mm en 24h. Score de risque : 88/100. Évacuation préventive recommandée.',
                'lat': 5.285, 'lng': -3.973, 'hours_ago': 2,
            },
            {
                'zone': 'ABJ-N', 'sev': 'danger', 'cat': 'road',
                'title': 'Route critique — Rue des Jardins',
                'msg':   'Score en chute : 45 → 23/100. Déformation importante de la chaussée.',
                'lat': 5.380, 'lng': -4.016, 'hours_ago': 4,
            },
            {
                'zone': 'BOU', 'sev': 'warning', 'cat': 'flood',
                'title': 'Montée des eaux — Bouaké Nord',
                'msg':   'Risque élevé détecté après analyse satellitaire. Score : 65/100. Pluviométrie : 95 mm.',
                'lat': 7.696, 'lng': -5.033, 'hours_ago': 5,
            },
            {
                'zone': 'ABJ-S', 'sev': 'warning', 'cat': 'vegetation',
                'title': 'Déforestation détectée — Mangroves de Vridi',
                'msg':   'Baisse NDVI de -0.07. Perte de couverture végétale significative détectée.',
                'lat': 5.287, 'lng': -3.986, 'hours_ago': 6,
            },
            {
                'zone': 'BOU', 'sev': 'danger', 'cat': 'road',
                'title': 'Boulevard de la Paix dégradé',
                'msg':   'Score : 31/100. Nids-de-poule importants. Intervention recommandée sous 72h.',
                'lat': 7.690, 'lng': -5.029, 'hours_ago': 8,
            },
            {
                'zone': 'YAM', 'sev': 'warning', 'cat': 'vegetation',
                'title': 'Recul végétation — Savane Yamoussoukro',
                'msg':   'NDVI en recul (-0.05 vs mois précédent). Surveillance accrue recommandée.',
                'lat': 6.820, 'lng': -5.282, 'hours_ago': 10,
            },
            {
                'zone': None, 'sev': 'info', 'cat': 'system',
                'title': 'Rapport hebdomadaire généré',
                'msg':   'Analyse géospatiale du 02/03/2026 disponible. 4 zones analysées, 11 alertes actives.',
                'lat': None, 'lng': None, 'hours_ago': 12,
            },
        ]
        for ad in alerts_data:
            zone_obj = zones.get(ad['zone']) if ad['zone'] else None
            Alert.objects.create(
                zone=zone_obj,
                title=ad['title'],
                message=ad['msg'],
                severity=ad['sev'],
                category=ad['cat'],
                lat=ad['lat'],
                lng=ad['lng'],
                created_at=now - timedelta(hours=ad['hours_ago']),
            )

        # ── RÉSUMÉ ──
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Données de démonstration chargées avec succès !\n'
            f'   • {Zone.objects.count()} zones\n'
            f'   • {RoadSegment.objects.count()} segments routiers\n'
            f'   • {FloodRisk.objects.count()} zones d\'inondation\n'
            f'   • {VegetationDensity.objects.count()} zones de végétation\n'
            f'   • {Alert.objects.count()} alertes\n\n'
            f'   👉 Lancez : python manage.py runserver\n'
            f'   👉 Ouvrez : http://127.0.0.1:8000/\n'
        ))
