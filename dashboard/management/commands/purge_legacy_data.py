"""
purge_legacy_data — Épure la base des données héritées des anciennes sources
(OSM/Overpass, HDX), désormais abandonnées.

SUPPRIME :
  dashboard        : Alert, RoadSegment, FloodRisk, VegetationDensity, Zone
                     (tout l'ancien monde OSM : ~170 zones, ~32k segments…)
  admin_divisions  : SousPrefecture, Departement, Region, Ville, District
                     (toute la hiérarchie — HDX comme dérivée).

CONSERVE :
  admin_divisions  : Commune UNIQUEMENT (source : communes_grand_abidjan.geojson).

Les FK Commune → District/Ville/Région/Département/Sous-préfecture (PROTECT)
sont remises à NULL avant suppression de ces niveaux.

Sans --yes, la commande liste les volumes concernés et NE SUPPRIME RIEN.

Usage :
    python manage.py purge_legacy_data          # aperçu (dry-run)
    python manage.py purge_legacy_data --yes    # purge effective
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from admin_divisions.models import (
    Commune,
    Departement,
    District,
    Region,
    SousPrefecture,
    Ville,
)
from dashboard.models import (
    Alert,
    FloodRisk,
    RoadSegment,
    VegetationDensity,
    Zone,
)


class Command(BaseCommand):
    help = (
        "Épure la base des données héritées OSM/HDX. Conserve uniquement "
        "les Communes (GeoJSON Grand Abidjan). "
        "Dry-run par défaut ; --yes pour supprimer."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true",
            help="Exécute réellement la purge (sinon : aperçu seul).",
        )

    def handle(self, *args, **options):
        apply = options["yes"]

        # (libellé, queryset) dans l'ordre de suppression — enfants d'abord.
        targets = [
            ("Alertes",           Alert.objects.all()),
            ("Segments routiers", RoadSegment.objects.all()),
            ("Zones d'inondation", FloodRisk.objects.all()),
            ("Végétation",        VegetationDensity.objects.all()),
            ("Zones (legacy)",    Zone.objects.all()),
            ("Sous-préfectures",  SousPrefecture.objects.all()),
            ("Départements",      Departement.objects.all()),
            ("Régions",           Region.objects.all()),
            ("Villes",            Ville.objects.all()),
            ("Districts",         District.objects.all()),
        ]

        self.stdout.write(self.style.HTTP_INFO("── État de la base ──"))
        for label, qs in targets:
            self.stdout.write(f"  {label:22s} : {qs.count():>7,} à supprimer")
        self.stdout.write(self.style.HTTP_INFO("── Conservé ──"))
        self.stdout.write(f"  {'Communes':22s} : {Commune.objects.count():>7,}")

        if not apply:
            self.stdout.write(self.style.WARNING(
                "\nAperçu seul — rien n'a été supprimé. "
                "Relance avec --yes pour purger."
            ))
            return

        with transaction.atomic():
            # Détacher toutes les FK PROTECT des communes avant de supprimer
            # la hiérarchie.
            n_com = Commune.objects.exclude(
                district=None, ville=None, region=None,
                departement=None, sous_prefecture=None,
            ).update(
                district=None, ville=None, region=None,
                departement=None, sous_prefecture=None,
            )
            if n_com:
                self.stdout.write(f"  FK détachées : {n_com} commune(s)")

            self.stdout.write(self.style.HTTP_INFO("\n── Purge ──"))
            for label, qs in targets:
                deleted, _ = qs.delete()
                self.stdout.write(f"  {label:22s} : {deleted:>7,} supprimé(s)")

        self.stdout.write(self.style.SUCCESS(
            f"\nPurge terminée. Base épurée — restent "
            f"{Commune.objects.count()} communes (aucune autre entité)."
        ))
