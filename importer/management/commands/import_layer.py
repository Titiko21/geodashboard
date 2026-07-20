"""
import_layer — Importe n'importe quel fichier géospatial (GeoJSON,
Shapefile, GeoPackage…) vers une couche de l'application, piloté par un
fichier de mapping JSON.

Usage :
    # Explorer un fichier (couches, colonnes) avant d'écrire le mapping
    python manage.py import_layer --file donnees.gpkg --list-layers

    # Prévisualiser sans écrire
    python manage.py import_layer --file communes.geojson \\
        --mapping importer/mappings/communes_grand_abidjan.json --dry-run

    # Importer
    python manage.py import_layer --file communes.geojson \\
        --mapping importer/mappings/communes_grand_abidjan.json

Cibles disponibles : cf. importer/registry.py (Phase A : commune).
"""
from django.core.management.base import BaseCommand, CommandError

from importer.engine import ImporterError, list_layers, run_import
from importer.registry import available_targets


class Command(BaseCommand):
    help = (
        "Importe un fichier géospatial (GeoJSON/SHP/GPKG…) vers une couche "
        "de l'application via un mapping JSON. Idempotent. "
        f"Cibles : {', '.join(available_targets())}."
    )

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True,
                            help="Chemin du fichier géospatial à importer.")
        parser.add_argument("--mapping",
                            help="Chemin du fichier de mapping JSON "
                                 "(requis sauf avec --list-layers).")
        parser.add_argument("--layer",
                            help="Nom de la couche à lire (défaut : première). "
                                 "Utile pour les GeoPackages multi-couches.")
        parser.add_argument("--list-layers", action="store_true",
                            help="Affiche couches et colonnes du fichier, puis quitte.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Prévisualise sans écrire en base.")
        parser.add_argument("--no-recompute", action="store_true",
                            help="N'exécute pas le travail de suite de la cible "
                                 "(ex. recalcul de la susceptibilité après un "
                                 "import de relevés d'inondation).")

    def handle(self, *args, **options):
        try:
            if options["list_layers"]:
                self._list_layers(options["file"])
                return
            if not options["mapping"]:
                raise CommandError("--mapping est requis (sauf avec --list-layers).")

            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("── DRY RUN ── aucune écriture\n"))

            report = run_import(
                file_path=options["file"],
                mapping_path=options["mapping"],
                layer_name=options["layer"],
                dry_run=options["dry_run"],
                log=self.stdout.write,
                run_hooks=not options["no_recompute"],
            )
        except ImporterError as exc:
            raise CommandError(str(exc))

        suffix = " (DRY RUN)" if report.dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 50}"))
        self.stdout.write(self.style.SUCCESS(f"Import terminé{suffix}"))
        self.stdout.write(f"  Créées      : {report.created}")
        self.stdout.write(f"  Mises à jour: {report.updated}")
        self.stdout.write(f"  Ignorées    : {len(report.skipped)}")

    def _list_layers(self, file_path):
        try:
            layers = list_layers(file_path)
        except ImporterError as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"Couches de {file_path} :")
        for name, count, fields in layers:
            self.stdout.write(f"  • {name} — {count} feature(s)")
            self.stdout.write(f"    colonnes : {', '.join(fields) or '(aucune)'}")
