"""
update_flood_susceptibility — Calcule (ou recalcule) la susceptibilité aux
inondations de chaque commune à partir des facteurs physiographiques GEE.

Multi-critères (cf. flood/scoring.py) : HAND, altitude, pente,
imperméabilisation, eau de surface — pondérations provisoires AHP.

Mise à jour MANUELLE (pas de scheduler — décision projet). Idempotent :
un enregistrement par commune, écrasé à chaque exécution.

Usage :
    python manage.py update_flood_susceptibility            # les 14 communes
    python manage.py update_flood_susceptibility --commune CIV-COM-COCODY
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from admin_divisions.models import Commune
from flood import scoring
from flood.gee_factors import get_physio_factors
from flood.models import CommuneFloodSusceptibility


class Command(BaseCommand):
    help = (
        "Calcule la susceptibilité inondation par commune (facteurs GEE : "
        "HAND, altitude, pente, bâti, eau). Manuel, idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--commune",
                            help="Code d'une seule commune (ex. CIV-COM-COCODY).")

    def handle(self, *args, **options):
        qs = Commune.objects.exclude(geom=None).order_by("name")
        if options["commune"]:
            qs = qs.filter(code=options["commune"])
            if not qs.exists():
                raise CommandError(f"Commune inconnue : {options['commune']}")

        ok, failed = 0, []
        for commune in qs:
            factors = get_physio_factors(json.loads(commune.geom.geojson))
            result = scoring.compute(factors) if factors else None
            if result is None:
                failed.append(commune.name)
                self.stderr.write(f"  ✗ {commune.name} : facteurs indisponibles (GEE ?)")
                continue

            scores = result["scores"]
            CommuneFloodSusceptibility.objects.update_or_create(
                commune=commune,
                defaults={
                    "elevation_mean_m": factors.get("elevation_mean_m"),
                    "elevation_min_m":  factors.get("elevation_min_m"),
                    "slope_mean_deg":   factors.get("slope_mean_deg"),
                    "hand_mean_m":      factors.get("hand_mean_m"),
                    "urban_pct":        factors.get("urban_pct"),
                    "water_pct":        factors.get("water_pct"),
                    "score_hand":       scores.get("hand"),
                    "score_elevation":  scores.get("elevation"),
                    "score_slope":      scores.get("slope"),
                    "score_impervious": scores.get("impervious"),
                    "score_water":      scores.get("water"),
                    "susceptibility":   result["susceptibility"],
                    "level":            result["level"],
                    "computed_at":      timezone.now(),
                },
            )
            ok += 1
            self.stdout.write(
                f"  ✓ {commune.name:14s} → {result['susceptibility']:5.1f}/100 "
                f"({result['level']}) | HAND {factors.get('hand_mean_m')} m · "
                f"alt {factors.get('elevation_mean_m')} m · "
                f"pente {factors.get('slope_mean_deg')}° · "
                f"bâti {factors.get('urban_pct')} % · eau {factors.get('water_pct')} %"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n{ok} commune(s) calculée(s), {len(failed)} échec(s)."
        ))
        if failed:
            self.stdout.write(self.style.WARNING("Échecs : " + ", ".join(failed)))
