"""
sync_gee.py — Commande Django de synchronisation Google Earth Engine
=====================================================================
Usage :
    python manage.py sync_gee                  → toutes les zones
    python manage.py sync_gee --zone ABJ-N     → une zone spécifique
    python manage.py sync_gee --layer ndvi     → un type d'analyse
    python manage.py sync_gee --dry-run        → test sans sauvegarder
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from dashboard.models import Zone, VegetationDensity, FloodRisk, RoadSegment, Alert
from dashboard.gee_client import (
    init_gee, test_connection,
    get_ndvi_stats, get_flood_risk_stats, get_road_condition_score,
    bbox_to_geojson,
)


# Décalage en degrés pour la bounding box autour du centre d'une zone (~11km)
ZONE_DELTA = 0.10


class Command(BaseCommand):
    help = 'Synchronise les données géospatiales depuis Google Earth Engine'

    def add_arguments(self, parser):
        parser.add_argument(
            '--zone',
            type=str, default=None,
            help='Code de la zone à synchroniser (ex: ABJ-N). Défaut: toutes.'
        )
        parser.add_argument(
            '--layer',
            type=str, default='all',
            choices=['all', 'ndvi', 'flood', 'roads'],
            help='Type d\'analyse à lancer. Défaut: all.'
        )
        parser.add_argument(
            '--date-start',
            type=str, default='2025-01-01',
            help='Date de début pour les images (YYYY-MM-DD). Défaut: 2025-01-01.'
        )
        parser.add_argument(
            '--date-end',
            type=str, default='2025-12-31',
            help='Date de fin pour les images (YYYY-MM-DD). Défaut: 2025-12-31.'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Tester la connexion et afficher ce qui serait fait, sans sauvegarder.'
        )

    def handle(self, *args, **options):
        zone_code  = options['zone']
        layer      = options['layer']
        date_start = options['date_start']
        date_end   = options['date_end']
        dry_run    = options['dry_run']

        self.stdout.write('\n' + '═' * 55)
        self.stdout.write('  GéoDash — Synchronisation Google Earth Engine')
        self.stdout.write('═' * 55)

        # ── 1. Test de connexion ──
        self.stdout.write('\n🔐 Test de connexion GEE...')
        ok, msg = test_connection()
        if not ok:
            raise CommandError(
                f'\n✗ Connexion échouée : {msg}\n\n'
                f'Vérifiez dans votre fichier .env :\n'
                f'  GEE_SERVICE_ACCOUNT=votre-compte@projet.iam.gserviceaccount.com\n'
                f'  GEE_KEY_FILE=C:/chemin/vers/private-key.json\n'
            )
        self.stdout.write(self.style.SUCCESS(f'   {msg}'))

        if dry_run:
            self.stdout.write('\n[DRY RUN] Mode test — aucune donnée ne sera sauvegardée.\n')

        # ── 2. Sélection des zones ──
        zones = Zone.objects.all()
        if zone_code:
            zones = zones.filter(code=zone_code)
            if not zones.exists():
                raise CommandError(f'Zone "{zone_code}" introuvable en base.')

        self.stdout.write(f'\n📍 {zones.count()} zone(s) à analyser | Couche : {layer}')
        self.stdout.write(f'   Période : {date_start} → {date_end}\n')

        stats = {'ok': 0, 'errors': 0, 'alerts': 0}

        # ── 3. Boucle sur les zones ──
        for zone in zones:
            self.stdout.write(f'\n┌─ 🛰  {zone.name} ({zone.code})')

            # Bounding box autour du centre de la zone
            d = ZONE_DELTA
            bounds = (
                zone.lng_center - d,
                zone.lat_center - d,
                zone.lng_center + d,
                zone.lat_center + d,
            )

            # ── NDVI ──
            if layer in ('all', 'ndvi'):
                self._sync_ndvi(zone, bounds, date_start, date_end, dry_run, stats)

            # ── Inondation ──
            if layer in ('all', 'flood'):
                self._sync_flood(zone, bounds, date_start, date_end, dry_run, stats)

            # ── Routes ──
            if layer in ('all', 'roads'):
                self._sync_roads(zone, date_start, date_end, dry_run, stats)

            self.stdout.write(f'└─ Terminé')

        # ── 4. Résumé ──
        self.stdout.write('\n' + '═' * 55)
        self.stdout.write(self.style.SUCCESS(
            f'✅ Synchronisation terminée\n'
            f'   • {stats["ok"]} analyse(s) réussies\n'
            f'   • {stats["alerts"]} alerte(s) générée(s)\n'
            f'   • {stats["errors"]} erreur(s)'
        ))
        if dry_run:
            self.stdout.write('   ℹ Mode dry-run : aucune donnée sauvegardée.')
        self.stdout.write('═' * 55 + '\n')

    # ── Méthodes privées ─────────────────────────────────

    def _sync_ndvi(self, zone, bounds, date_start, date_end, dry_run, stats):
        self.stdout.write('│  🌿 Analyse NDVI (Sentinel-2)...')
        try:
            result = get_ndvi_stats(*bounds, date_start=date_start, date_end=date_end)

            self.stdout.write(
                f'│     NDVI moyen   : {result["ndvi_mean"]:+.3f}\n'
                f'│     Couverture   : {result["coverage_pct"]}%\n'
                f'│     Densité      : {result["density_class"]}\n'
                f'│     Image du     : {result["image_date"]}'
            )

            if not dry_run:
                # Récupérer l'ancienne valeur pour calculer la variation
                old = VegetationDensity.objects.filter(zone=zone).first()
                old_ndvi = old.ndvi_value if old else result['ndvi_mean']
                change   = round(result['ndvi_mean'] - old_ndvi, 3)

                VegetationDensity.objects.update_or_create(
                    zone=zone,
                    name=f'Végétation — {zone.name}',
                    defaults={
                        'ndvi_value':          result['ndvi_mean'],
                        'density_class':       result['density_class'],
                        'coverage_percent':    result['coverage_pct'],
                        'change_vs_previous':  change,
                        'last_analyzed':       timezone.now(),
                        'geojson':             bbox_to_geojson(*bounds),
                    }
                )
                # Alerte si forte déforestation
                if change <= -0.05:
                    Alert.objects.create(
                        zone=zone,
                        title=f'Déforestation détectée — {zone.name}',
                        message=(
                            f'NDVI en baisse de {change:.3f} depuis la dernière analyse. '
                            f'Couverture actuelle : {result["coverage_pct"]}%.'
                        ),
                        severity='warning',
                        category='vegetation',
                        lat=zone.lat_center, lng=zone.lng_center,
                    )
                    stats['alerts'] += 1

            stats['ok'] += 1

        except Exception as e:
            self.stderr.write(f'│  ✗ Erreur NDVI : {e}')
            stats['errors'] += 1

    def _sync_flood(self, zone, bounds, date_start, date_end, dry_run, stats):
        self.stdout.write('│  🌊 Analyse inondation (Sentinel-1 SAR)...')
        try:
            result = get_flood_risk_stats(*bounds, date_start=date_start, date_end=date_end)

            self.stdout.write(
                f'│     Score risque : {result["risk_score"]}/100\n'
                f'│     Niveau       : {result["risk_level"]}\n'
                f'│     VV moyen     : {result["vv_mean_db"]} dB\n'
                f'│     Surface eau  : {result["water_pct"]}%\n'
                f'│     Précip.      : {result["rainfall_mm"]} mm'
            )

            if not dry_run:
                d = 0.10
                FloodRisk.objects.update_or_create(
                    zone=zone,
                    name=f'Zone inondation — {zone.name}',
                    defaults={
                        'risk_level':    result['risk_level'],
                        'risk_score':    result['risk_score'],
                        'area_km2':      round((2 * d * 111) ** 2, 1),
                        'rainfall_mm':   result['rainfall_mm'],
                        'last_analyzed': timezone.now(),
                        'geojson':       bbox_to_geojson(*bounds),
                    }
                )
                # Alerte si risque élevé
                if result['risk_score'] >= 65:
                    sev = 'critical' if result['risk_score'] >= 80 else 'danger'
                    Alert.objects.create(
                        zone=zone,
                        title=f'Risque inondation {result["risk_level"]} — {zone.name}',
                        message=(
                            f'Score GEE : {result["risk_score"]}/100. '
                            f'Surface d\'eau détectée : {result["water_pct"]}%. '
                            f'Précipitations : {result["rainfall_mm"]} mm.'
                        ),
                        severity=sev,
                        category='flood',
                        lat=zone.lat_center, lng=zone.lng_center,
                    )
                    stats['alerts'] += 1

            stats['ok'] += 1

        except Exception as e:
            self.stderr.write(f'│  ✗ Erreur inondation : {e}')
            stats['errors'] += 1

    def _sync_roads(self, zone, date_start, date_end, dry_run, stats):
        roads = RoadSegment.objects.filter(zone=zone)
        if not roads.exists():
            self.stdout.write('│  🛣  Aucune route à analyser dans cette zone.')
            return

        self.stdout.write(f'│  🛣  Analyse de {roads.count()} route(s)...')

        for road in roads:
            if not road.geojson or road.geojson.get('type') != 'LineString':
                continue
            try:
                coords = road.geojson['coordinates']
                result = get_road_condition_score(
                    coords, date_start=date_start, date_end=date_end
                )

                self.stdout.write(
                    f'│     [{road.name[:30]:30s}] '
                    f'Score: {result["condition_score"]:5.1f} | '
                    f'Statut: {result["status"]}'
                )

                if not dry_run:
                    old_score  = road.condition_score
                    road.condition_score = result['condition_score']
                    road.status          = result['status']
                    road.last_analyzed   = timezone.now()
                    road.save()

                    # Alerte si dégradation soudaine (baisse > 20 points)
                    if old_score - result['condition_score'] > 20:
                        Alert.objects.create(
                            zone=zone,
                            title=f'Dégradation route — {road.name}',
                            message=(
                                f'Score en chute : {old_score:.0f} → {result["condition_score"]:.0f}/100. '
                                f'Statut : {road.get_status_display()}.'
                            ),
                            severity='danger',
                            category='road',
                            lat=zone.lat_center, lng=zone.lng_center,
                        )
                        stats['alerts'] += 1

                stats['ok'] += 1

            except Exception as e:
                self.stderr.write(f'│  ✗ Erreur route "{road.name}" : {e}')
                stats['errors'] += 1
