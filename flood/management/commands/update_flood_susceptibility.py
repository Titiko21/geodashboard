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
from flood.models import CommuneFloodSusceptibility, FloodEvent


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
            # Inondations observées intersectant la commune (0 si aucune) —
            # elles imposent un plancher, uniquement à la hausse.
            n_events = FloodEvent.objects.filter(geom__intersects=commune.geom).count()

            result = scoring.compute(factors, n_events) if factors else None
            if result is None:
                failed.append(commune.name)
                self.stderr.write(f"  ✗ {commune.name} : facteurs indisponibles (GEE ?)")
                continue

            scores = result["scores"]
            CommuneFloodSusceptibility.objects.update_or_create(
                commune=commune,
                defaults={
                    "elevation_mean_m":      factors.get("elevation_mean_m"),
                    "elevation_min_m":       factors.get("elevation_min_m"),
                    "slope_mean_deg":        factors.get("slope_mean_deg"),
                    "hand_mean_m":           factors.get("hand_mean_m"),
                    "hand_low_pct":          factors.get("hand_low_pct"),
                    "flat_pct":              factors.get("flat_pct"),
                    "built_low_pct":         factors.get("built_low_pct"),
                    "history_events":        n_events,
                    "urban_pct":             factors.get("urban_pct"),
                    "water_pct":             factors.get("water_pct"),
                    "score_hand_low":        scores.get("hand_low"),
                    "score_exposure":        scores.get("exposure"),
                    "score_elevation":       scores.get("elevation"),
                    "score_impervious":      scores.get("impervious"),
                    "score_water":           scores.get("water"),
                    "score_flat":            scores.get("flat"),
                    "physio_susceptibility": result["physio"],
                    "susceptibility":        result["susceptibility"],
                    "level":                 result["level"],
                    "computed_at":           timezone.now(),
                },
            )
            ok += 1
            flag = "  ← relevé par l'historique" if result["raised_by_history"] else ""
            self.stdout.write(
                f"  ✓ {commune.name:14s} → {result['susceptibility']:5.1f}/100 "
                f"({result['level']:8s}) | terrain {result['physio']:5.1f} · "
                f"inondations observées {n_events}{flag}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n{ok} commune(s) calculée(s), {len(failed)} échec(s)."
        ))
        if failed:
            self.stdout.write(self.style.WARNING("Échecs : " + ", ".join(failed)))
