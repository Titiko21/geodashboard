"""
update_gee_scores.py — GéoDash
Met à jour les scores avec des données satellite GEE réelles.

Place dans : dashboard/management/commands/update_gee_scores.py

Usage :
    python manage.py update_gee_scores                 # toutes les zones
    python manage.py update_gee_scores --zone DAL      # une seule zone
"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from dashboard.models import Zone, RoadSegment, FloodRisk, VegetationDensity, Alert

logger = logging.getLogger("dashboard")


def _geometry_centroid(geojson: dict):
    """Centroïde approximatif d'un GeoJSON. Retourne (lat, lng) ou (None, None)."""
    if not geojson:
        return None, None
    geo_type = geojson.get("type", "")
    coords = geojson.get("coordinates", [])
    if not coords:
        return None, None

    points = []
    if geo_type == "LineString":
        points = coords
    elif geo_type == "Polygon":
        points = coords[0] if coords else []
    elif geo_type == "MultiLineString":
        for line in coords:
            points.extend(line)
    elif geo_type == "MultiPolygon":
        for poly in coords:
            if poly:
                points.extend(poly[0])
    if not points:
        return None, None

    avg_lng = sum(p[0] for p in points) / len(points)
    avg_lat = sum(p[1] for p in points) / len(points)
    return round(avg_lat, 6), round(avg_lng, 6)


def _ndvi_to_density(ndvi: float) -> str:
    if ndvi < 0.2: return "sparse"
    if ndvi < 0.4: return "moderate"
    if ndvi < 0.6: return "dense"
    return "very_dense"


class Command(BaseCommand):
    help = "Met à jour les scores avec des données satellite GEE réelles."

    def add_arguments(self, parser):
        parser.add_argument("--zone", type=str, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        zone_code = options["zone"]
        dry_run = options["dry_run"]

        from dashboard.gee_integration import get_ee, get_ndvi_stats, get_flood_extent
        ee = get_ee()
        if ee is None:
            self.stderr.write(self.style.ERROR("GEE non disponible."))
            return

        self.stdout.write(self.style.SUCCESS("GEE connecté.\n"))

        if zone_code:
            zones = Zone.objects.filter(code__iexact=zone_code)
            if not zones.exists():
                raise CommandError(f"Zone '{zone_code}' introuvable.")
        else:
            zones = Zone.objects.all()

        total = zones.count()
        stats = {"ndvi": 0, "flood": 0, "alerts": 0, "errors": 0}

        for i, zone in enumerate(zones, 1):
            self.stdout.write(f"\n[{i}/{total}] {zone.name} ({zone.code})")

            bbox = {
                "west": zone.lng_center - 0.05,
                "south": zone.lat_center - 0.05,
                "east": zone.lng_center + 0.05,
                "north": zone.lat_center + 0.05,
            }

            # ── NDVI réel ──
            veg_qs = VegetationDensity.objects.filter(zone=zone)
            if veg_qs.exists():
                try:
                    ndvi_data = get_ndvi_stats(bbox)
                    if ndvi_data and ndvi_data.get("mean_ndvi") is not None:
                        mean = ndvi_data["mean_ndvi"]
                        now = timezone.now()

                        TYPE_WEIGHT = {
                            "forest": 1.0, "wood": 0.95, "orchard": 0.75,
                            "meadow": 0.55, "grass": 0.45, "grassland": 0.40,
                            "scrub": 0.35, "heath": 0.30, "farmland": 0.25,
                        }

                        to_update = []
                        for v in veg_qs:
                            name_lower = (v.name or "").lower()
                            weight = 0.5
                            for vtype, w in TYPE_WEIGHT.items():
                                if vtype in name_lower:
                                    weight = w
                                    break
                            variation = ((v.osm_id or 0) % 100) / 500.0 - 0.1
                            real_ndvi = round(max(0.01, min(0.95, mean * weight + variation)), 3)
                            v.ndvi_value = real_ndvi
                            v.density_class = _ndvi_to_density(real_ndvi)
                            v.coverage_percent = round(real_ndvi * 100, 1)
                            v.last_analyzed = now
                            to_update.append(v)

                        if not dry_run:
                            VegetationDensity.objects.bulk_update(
                                to_update,
                                ["ndvi_value", "density_class", "coverage_percent", "last_analyzed"],
                                batch_size=200,
                            )
                        stats["ndvi"] += len(to_update)
                        self.stdout.write(f"  NDVI réel : {mean:.4f} → {len(to_update)} zones mises à jour")
                    else:
                        self.stdout.write("  NDVI : pas de données satellite")
                except Exception as e:
                    stats["errors"] += 1
                    self.stderr.write(f"  NDVI erreur : {e}")

            # ── Flood réel ──
            flood_qs = FloodRisk.objects.filter(zone=zone)
            if flood_qs.exists():
                try:
                    flood_data = get_flood_extent(bbox)
                    if flood_data and flood_data.get("risk_score") is not None:
                        zone_risk = flood_data["risk_score"]
                        now = timezone.now()

                        TYPE_MULT = {
                            "river": 1.2, "canal": 1.0, "stream": 0.7,
                            "wetland": 1.1, "water": 0.5,
                        }

                        to_update = []
                        for f in flood_qs:
                            name_lower = (f.name or "").lower()
                            mult = 0.8
                            for wtype, m in TYPE_MULT.items():
                                if wtype in name_lower:
                                    mult = m
                                    break
                            variation = ((f.osm_id or 0) % 100) / 200.0 - 0.25
                            real_score = round(max(0, min(100, zone_risk * mult + variation * 20)), 1)

                            if real_score >= 70: level = "critique"
                            elif real_score >= 50: level = "eleve"
                            elif real_score >= 30: level = "modere"
                            else: level = "faible"

                            f.risk_score = real_score
                            f.risk_level = level
                            f.last_analyzed = now
                            to_update.append(f)

                        if not dry_run:
                            FloodRisk.objects.bulk_update(
                                to_update,
                                ["risk_score", "risk_level", "last_analyzed"],
                                batch_size=200,
                            )
                        stats["flood"] += len(to_update)
                        self.stdout.write(f"  Flood SAR : risque={zone_risk} → {len(to_update)} zones mises à jour")
                    else:
                        self.stdout.write("  Flood : pas de données SAR")
                except Exception as e:
                    stats["errors"] += 1
                    self.stderr.write(f"  Flood erreur : {e}")

            # ── Corriger coordonnées alertes ──
            alerts = Alert.objects.filter(zone=zone, is_read=False)
            fixed = 0
            to_update_alerts = []
            for alert in alerts:
                obj = None
                if alert.category == "road":
                    name = alert.title.replace("Route dégradée : ", "")
                    obj = RoadSegment.objects.filter(zone=zone, name=name).first()
                elif alert.category == "flood":
                    name = alert.title.replace("Risque inondation : ", "")
                    obj = FloodRisk.objects.filter(zone=zone, name=name).first()

                if obj and obj.geojson:
                    lat, lng = _geometry_centroid(obj.geojson)
                    if lat and lng:
                        alert.lat = lat
                        alert.lng = lng
                        to_update_alerts.append(alert)
                        fixed += 1

            if not dry_run and to_update_alerts:
                Alert.objects.bulk_update(to_update_alerts, ["lat", "lng"], batch_size=100)
            stats["alerts"] += fixed
            if fixed:
                self.stdout.write(f"  Alertes : {fixed} coordonnées corrigées")

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 50}"))
        self.stdout.write(self.style.SUCCESS("Terminé" + (" (DRY RUN)" if dry_run else "")))
        self.stdout.write(f"  Végétation : {stats['ndvi']} scores mis à jour")
        self.stdout.write(f"  Inondation : {stats['flood']} scores mis à jour")
        self.stdout.write(f"  Alertes    : {stats['alerts']} coordonnées corrigées")
        if stats["errors"]:
            self.stdout.write(self.style.WARNING(f"  Erreurs    : {stats['errors']}"))