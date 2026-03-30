"""
check_missing.py — Commande Django : identifie et réimporte les zones sans données.

Place dans : dashboard/management/commands/check_missing.py

Usage :
    python manage.py check_missing                  # diagnostic seul
    python manage.py check_missing --fix             # réimporte les zones manquantes
    python manage.py check_missing --fix --type roads # routes uniquement
"""
import logging

from django.core.management.base import BaseCommand
from django.db.models import Count

from dashboard.models import Zone, RoadSegment, FloodRisk, VegetationDensity

logger = logging.getLogger("dashboard")


class Command(BaseCommand):
    help = "Identifie les zones sans données et propose un réimport ciblé."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Lance automatiquement populate_geodata sur les zones manquantes.",
        )
        parser.add_argument(
            "--type", type=str, default="all",
            choices=["all", "roads", "flood", "vegetation"],
            help="Type de données à vérifier / réimporter.",
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        data_type = options["type"]

        total_zones = Zone.objects.count()
        if total_zones == 0:
            self.stderr.write(self.style.ERROR(
                "Aucune zone en base. Lancez d'abord : python manage.py populate_geodata"
            ))
            return

        self.stdout.write(f"\nTotal zones en base : {total_zones}\n")

        # ── Zones sans routes ──
        missing_roads = []
        if data_type in ("all", "roads"):
            zones_roads = (
                Zone.objects.annotate(n=Count("roads"))
                .filter(n=0)
                .values_list("code", "name")
            )
            missing_roads = list(zones_roads)
            self.stdout.write(
                self.style.WARNING(f"\nZones SANS routes : {len(missing_roads)}/{total_zones}")
            )
            for code, name in missing_roads[:30]:
                self.stdout.write(f"  - {code:6s} {name}")
            if len(missing_roads) > 30:
                self.stdout.write(f"  ... et {len(missing_roads) - 30} autres")

        # ── Zones sans inondations ──
        missing_flood = []
        if data_type in ("all", "flood"):
            zones_flood = (
                Zone.objects.annotate(n=Count("flood_risks"))
                .filter(n=0)
                .values_list("code", "name")
            )
            missing_flood = list(zones_flood)
            self.stdout.write(
                self.style.WARNING(f"\nZones SANS inondations : {len(missing_flood)}/{total_zones}")
            )

        # ── Zones sans végétation ──
        missing_veg = []
        if data_type in ("all", "vegetation"):
            zones_veg = (
                Zone.objects.annotate(n=Count("vegetation"))
                .filter(n=0)
                .values_list("code", "name")
            )
            missing_veg = list(zones_veg)
            self.stdout.write(
                self.style.WARNING(f"\nZones SANS végétation : {len(missing_veg)}/{total_zones}")
            )

        # ── Récap ──
        all_missing_codes = set()
        if data_type in ("all", "roads"):
            all_missing_codes.update(code for code, _ in missing_roads)
        if data_type in ("all", "flood"):
            all_missing_codes.update(code for code, _ in missing_flood)
        if data_type in ("all", "vegetation"):
            all_missing_codes.update(code for code, _ in missing_veg)

        if not all_missing_codes:
            self.stdout.write(self.style.SUCCESS("\n✓ Toutes les zones ont des données !"))
            return

        self.stdout.write(
            f"\nTotal zones à traiter : {len(all_missing_codes)}"
        )

        if not fix:
            self.stdout.write(
                self.style.NOTICE(
                    "\nAjoutez --fix pour lancer le réimport automatique.\n"
                    "Ou lancez manuellement :\n"
                )
            )
            # Génère les commandes manuelles
            batch_size = 5
            codes = sorted(all_missing_codes)
            for i in range(0, len(codes), batch_size):
                batch = codes[i:i + batch_size]
                for code in batch:
                    self.stdout.write(f"  python manage.py populate_geodata --zone {code}")
            return

        # ── Fix automatique ──
        self.stdout.write(self.style.HTTP_INFO(
            f"\nRéimport de {len(all_missing_codes)} zones...\n"
        ))

        from django.core.management import call_command

        roads_only = data_type == "roads"
        success = 0
        errors = 0

        for code in sorted(all_missing_codes):
            try:
                self.stdout.write(f"\n→ Import {code}...")
                call_command(
                    "populate_geodata",
                    zone=code,
                    roads_only=roads_only,
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
                success += 1
            except Exception as e:
                errors += 1
                self.stderr.write(self.style.ERROR(f"  ✗ Erreur {code} : {e}"))
                logger.exception("check_missing --fix failed for zone %s", code)

        self.stdout.write(self.style.SUCCESS(
            f"\nTerminé : {success} réussies, {errors} en erreur"
        ))
