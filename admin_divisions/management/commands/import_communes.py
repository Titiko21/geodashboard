"""
import_communes — Importe les communes depuis le GeoJSON livré avec le
dépôt. C'est la SEULE source de données actuelle de l'application.

Client léger de l'importeur générique (app `importer`) : équivaut à

    python manage.py import_layer \\
        --file admin_divisions/data/communes_grand_abidjan.geojson \\
        --mapping importer/mappings/communes_grand_abidjan.json

Les communes sont importées SEULES : aucun district ni ville n'est créé —
les FK de rattachement restent NULL (raccrochées plus tard via la BDR).

Idempotent : upsert par `code` (CIV-COM-<SLUG>). Rejouer la commande ne
crée pas de doublon. Lancé à chaque démarrage du conteneur (entrypoint).

Usage :
    python manage.py import_communes
    python manage.py import_communes --file /chemin/communes.geojson
    python manage.py import_communes --dry-run
"""
from django.core.management.base import BaseCommand, CommandError

# Ré-exports : logique de domaine testée via ce module (cf. tests).
from admin_divisions.communes import (  # noqa: F401
    CANONICAL_NAMES,
    DEFAULT_FILE,
    canonical_name,
    commune_code,
    is_known_commune,
)
from importer import MAPPINGS_DIR
from importer.engine import ImporterError, run_import

MAPPING_FILE = MAPPINGS_DIR / "communes_grand_abidjan.json"


class Command(BaseCommand):
    help = (
        "Importe les communes (polygones) depuis un GeoJSON — seule source de "
        "données actuelle. Idempotent (update_or_create par code)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=str(DEFAULT_FILE),
            help=f"Chemin du GeoJSON (défaut : {DEFAULT_FILE}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simule sans écrire en base.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("── DRY RUN ── aucune écriture\n"))

        try:
            report = run_import(
                file_path=options["file"],
                mapping_path=MAPPING_FILE,
                dry_run=dry_run,
                log=self.stdout.write,
            )
        except ImporterError as exc:
            raise CommandError(str(exc))

        from admin_divisions.models import Commune

        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 50}"))
        self.stdout.write(self.style.SUCCESS(
            "Import communes terminé" + (" (DRY RUN)" if dry_run else "")
        ))
        self.stdout.write(
            f"  Communes    : {report.created} créées, {report.updated} mises à jour"
        )
        if report.skipped:
            self.stdout.write(f"  Ignorées    : {len(report.skipped)}")
        self.stdout.write(f"  Total base  : {Commune.objects.count()} communes")
