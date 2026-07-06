"""
Interface d'administration de l'importeur générique.

Permet à un administrateur d'importer une couche géospatiale (GeoJSON,
Shapefile multi-fichiers, GeoPackage…) SANS passer par la ligne de
commande : upload → choix du mapping → aperçu (dry-run) → import.

Les exports se font depuis les listes de l'admin Django (actions
« Exporter en CSV / GeoJSON » sur chaque modèle).
"""
import tempfile
from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from importer import MAPPINGS_DIR
from importer.engine import ImporterError, run_import
from importer.registry import available_targets

# Extensions acceptées pour le fichier principal d'une couche.
_MAIN_EXTS = (".geojson", ".json", ".gpkg", ".shp", ".kml")


def _shipped_mappings():
    return sorted(p.name for p in MAPPINGS_DIR.glob("*.json"))


@staff_member_required
def import_layer_view(request):
    ctx = {
        "title": "Importer une couche géospatiale",
        "mappings": _shipped_mappings(),
        "targets": available_targets(),
        "report": None,
        "log": [],
        "error": None,
    }

    if request.method == "POST":
        data_files = request.FILES.getlist("data_files")
        mapping_file = request.FILES.get("mapping_file")
        mapping_choice = request.POST.get("mapping_choice") or ""
        dry_run = bool(request.POST.get("dry_run"))
        ctx["dry_run"] = dry_run

        if not data_files:
            ctx["error"] = "Aucun fichier de données fourni."
            return render(request, "importer/import_layer.html", ctx)

        with tempfile.TemporaryDirectory(prefix="gd_import_") as tmp:
            tmpdir = Path(tmp)
            # Dépose tous les fichiers (un Shapefile = .shp + .dbf + .shx…).
            main_path = None
            for f in data_files:
                dest = tmpdir / Path(f.name).name
                with open(dest, "wb") as out:
                    for chunk in f.chunks():
                        out.write(chunk)
                if dest.suffix.lower() in _MAIN_EXTS and main_path is None:
                    main_path = dest
            if main_path is None:
                ctx["error"] = (
                    "Aucun fichier principal reconnu "
                    f"({', '.join(_MAIN_EXTS)}) parmi les fichiers déposés."
                )
                return render(request, "importer/import_layer.html", ctx)

            # Mapping : uploadé, sinon choisi parmi ceux livrés.
            if mapping_file:
                mapping_path = tmpdir / "mapping.json"
                with open(mapping_path, "wb") as out:
                    for chunk in mapping_file.chunks():
                        out.write(chunk)
            elif mapping_choice:
                mapping_path = MAPPINGS_DIR / mapping_choice
            else:
                ctx["error"] = "Choisissez un mapping livré ou déposez un mapping JSON."
                return render(request, "importer/import_layer.html", ctx)

            log_lines = []
            try:
                report = run_import(
                    file_path=main_path,
                    mapping_path=mapping_path,
                    dry_run=dry_run,
                    log=log_lines.append,
                )
                ctx["report"] = report
                ctx["log"] = log_lines
            except ImporterError as exc:
                ctx["error"] = str(exc)
                ctx["log"] = log_lines

    return render(request, "importer/import_layer.html", ctx)
