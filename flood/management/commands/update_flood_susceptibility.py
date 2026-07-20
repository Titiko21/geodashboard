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
from django.core.management.base import BaseCommand, CommandError

from admin_divisions.models import Commune
from flood.susceptibility import recompute_communes


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

        ok, failed = recompute_communes(qs, log=self.stdout.write)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{ok} commune(s) calculée(s), {len(failed)} échec(s)."
        ))
        if failed:
            self.stdout.write(self.style.WARNING("Échecs : " + ", ".join(failed)))
