"""
purge_legacy_data — Épure la base des données héritées des anciennes sources
(OSM/Overpass, HDX), désormais abandonnées.

SUPPRIME :
  dashboard        : Alert, RoadSegment, FloodRisk, VegetationDensity, Zone
                     (tout l'ancien monde OSM : ~170 zones, ~32k segments…)
  admin_divisions  : SousPrefecture, Departement, Region (hiérarchie HDX),
                     et les Districts qu'aucune Commune/Ville ne référence.

CONSERVE :
  admin_divisions  : Commune + Ville (source : communes_grand_abidjan.geojson)
                     et leurs Districts de rattachement (Abidjan, Comoé).

Les FK Commune/Ville → Région/Département/Sous-préfecture (PROTECT) sont
remises à NULL avant suppression de ces niveaux.

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
        "Communes + Villes (GeoJSON Grand Abidjan) et leurs districts. "
        "Dry-run par défaut ; --yes pour supprimer."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true",
            help="Exécute réellement la purge (sinon : aperçu seul).",
        )

    def handle(self, *args, **options):
        apply = options["yes"]

        # Districts encore référencés par les données conservées.
        kept_district_ids = set(
            Commune.objects.values_list("district_id", flat=True)
        ) | set(
            Ville.objects.values_list("district_id", flat=True)
        )
        districts_to_delete = District.objects.exclude(id__in=kept_district_ids)

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
            ("Districts orphelins", districts_to_delete),
        ]

        self.stdout.write(self.style.HTTP_INFO("── État de la base ──"))
        for label, qs in targets:
            self.stdout.write(f"  {label:22s} : {qs.count():>7,} à supprimer")
        self.stdout.write(self.style.HTTP_INFO("── Conservé ──"))
        self.stdout.write(f"  {'Villes':22s} : {Ville.objects.count():>7,}")
        self.stdout.write(f"  {'Communes':22s} : {Commune.objects.count():>7,}")
        self.stdout.write(
            f"  {'Districts conservés':22s} : {len(kept_district_ids):>7,}"
        )

        if not apply:
            self.stdout.write(self.style.WARNING(
                "\nAperçu seul — rien n'a été supprimé. "
                "Relance avec --yes pour purger."
            ))
            return

        with transaction.atomic():
            # Détacher les FK PROTECT avant de supprimer la hiérarchie HDX.
            n_com = Commune.objects.exclude(
                region=None, departement=None, sous_prefecture=None
            ).update(region=None, departement=None, sous_prefecture=None)
            n_vil = Ville.objects.exclude(
                region=None, departement=None, sous_prefecture=None
            ).update(region=None, departement=None, sous_prefecture=None)
            if n_com or n_vil:
                self.stdout.write(
                    f"  FK détachées : {n_com} commune(s), {n_vil} ville(s)"
                )

            self.stdout.write(self.style.HTTP_INFO("\n── Purge ──"))
            for label, qs in targets:
                deleted, _ = qs.delete()
                self.stdout.write(f"  {label:22s} : {deleted:>7,} supprimé(s)")

        self.stdout.write(self.style.SUCCESS(
            f"\nPurge terminée. Base épurée — restent "
            f"{Ville.objects.count()} villes, {Commune.objects.count()} communes, "
            f"{District.objects.count()} districts."
        ))
