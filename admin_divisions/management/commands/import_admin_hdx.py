"""
import_admin_hdx — Importe les frontières administratives depuis le ZIP HDX
(Common Operational Dataset — Administrative Boundaries).

Page officielle : https://data.humdata.org/dataset/cod-ab-civ

Particularité HDX Côte d'Ivoire :
  ADM1 contient **33 entités au même niveau** : 31 régions + 2 districts
  autonomes (Abidjan, Yamoussoukro). Les 12 districts non-autonomes
  (Lagunes, Vallée du Bandama, …) n'ont pas de géométrie HDX, on les
  reconstruit par ST_Union des régions enfantes.

Mapping appliqué (cf. admin_divisions/_hdx_utils.py / DISTRICT_REGIONS) :
  - 12 districts non-autonomes créés en code (géom = ST_Union de leurs régions)
  - 2 districts autonomes pris depuis HDX (géom HDX directe)
  - 31 régions prises depuis HDX, rattachées à leur district via le mapping

Usage :
    python manage.py import_admin_hdx --zip-path /chemin/local.zip
    python manage.py import_admin_hdx --zip-path X.zip --dry-run

Idempotent : update_or_create par code. Rejouer la commande ne crée pas
de doublons.
"""
import logging
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import requests
from django.contrib.gis.db.models import Union as GeomUnion
from django.contrib.gis.gdal import DataSource
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management.base import BaseCommand, CommandError

from admin_divisions._hdx_utils import (
    ADM1_NAME_CANDIDATES,
    ADM1_PCODE_CANDIDATES,
    ADM2_NAME_CANDIDATES,
    ADM2_PARENT_CANDIDATES,
    ADM2_PCODE_CANDIDATES,
    ADM3_NAME_CANDIDATES,
    ADM3_PARENT_CANDIDATES,
    ADM3_PCODE_CANDIDATES,
    DISTRICT_REGIONS,
    clean_autonomous_name,
    district_code,
    first_match,
    is_autonomous_name,
    normalize_name,
    region_to_district_map,
    to_multipolygon,
)
from admin_divisions.models import Departement, District, Region, SousPrefecture

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Importe District + Région + Département + Sous-préfecture depuis le "
        "ZIP HDX (cod-ab-civ). Gère la spécificité CI où ADM1 mélange régions "
        "et districts autonomes, et où certains départements (Abidjan, "
        "Yamoussoukro, Attiégouakro) n'ont pas de Région parente."
    )

    def add_arguments(self, parser):
        parser.add_argument("--zip-path", help="Chemin local d'un ZIP HDX déjà téléchargé.")
        parser.add_argument("--zip-url",  help="URL d'un ZIP HDX (download auto).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Affiche ce qui serait importé, n'écrit rien en base.")

    # ── Entry point ────────────────────────────────────────────────────────

    def handle(self, *args, **opts):
        zip_path = opts["zip_path"]
        zip_url  = opts["zip_url"]
        dry_run  = opts["dry_run"]

        if not zip_path and not zip_url:
            raise CommandError(
                "Fournir --zip-path /chemin/local.zip OU --zip-url https://...\n"
                "Page HDX : https://data.humdata.org/dataset/cod-ab-civ"
            )

        if zip_url and not zip_path:
            zip_path = self._download(zip_url)
        if not Path(zip_path).is_file():
            raise CommandError(f"ZIP introuvable : {zip_path}")

        extract_dir = tempfile.mkdtemp(prefix="hdx_admbnda_")
        self.stdout.write(f"Extraction du ZIP → {extract_dir}")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        sources = self._discover_sources(extract_dir)
        if not sources:
            raise CommandError(f"Aucune source GIS trouvée dans {extract_dir}.")

        self.stdout.write(f"Sources géo détectées ({len(sources)}) :")
        for label, _ in sources:
            self.stdout.write(f"  - {label}")

        self._import_adm1(sources, dry_run)
        self._import_adm2(sources, dry_run)
        self._import_adm3(sources, dry_run)

    # ── Import ADM1 — la grosse logique ────────────────────────────────────

    def _import_adm1(self, sources, dry_run):
        """
        Lit le layer ADM1 (33 entrées HDX) et répartit :
          - Les 2 entrées "District Autonome De/D'..." → table District (autonomes).
          - Les 31 autres → table Region, rattachées à leur district parent
            via le mapping DISTRICT_REGIONS.
        Crée aussi les 12 districts non-autonomes (sans géom dans un 1er temps),
        puis reconstruit leur géom par ST_Union des régions enfantes.
        """
        found = self._find_layer(sources, 1)
        if not found:
            self.stderr.write(self.style.ERROR("Aucun layer ADM1 trouvé."))
            return
        label, layer = found

        self.stdout.write(self.style.HTTP_INFO(f"\n=== ADM1 — {label} ==="))
        fields = list(layer.fields)
        self.stdout.write(f"  Colonnes : {fields}")

        name_field  = first_match(fields, ADM1_NAME_CANDIDATES)
        pcode_field = first_match(fields, ADM1_PCODE_CANDIDATES)
        if not (name_field and pcode_field):
            self.stderr.write(self.style.ERROR(
                f"Colonnes attendues introuvables dans {fields}"
            ))
            return
        self.stdout.write(f"  Mapping colonnes : name={name_field}, code={pcode_field}")

        # 1) Création des 12 districts non-autonomes (sans géom à ce stade)
        if not dry_run:
            for d_name in DISTRICT_REGIONS:
                District.objects.update_or_create(
                    code=district_code(d_name),
                    defaults={"name": d_name, "is_autonomous": False},
                )
            self.stdout.write(self.style.SUCCESS(
                f"  ✓ {len(DISTRICT_REGIONS)} districts non-autonomes créés "
                f"(géom à reconstituer en fin d'import)."
            ))

        region_to_district = region_to_district_map()

        # 2) Parcours des 33 ADM1 entrées
        autonomes_imported   = 0
        regions_imported     = 0
        unmatched_regions    = []
        for feat in layer:
            name  = str(feat.get(name_field) or "").strip()
            code  = str(feat.get(pcode_field) or "").strip()
            geom  = self._reproject_4326(feat.geom)
            mpoly = to_multipolygon(geom.geos if geom else None)

            if not (name and code and mpoly):
                continue

            if is_autonomous_name(name):
                short = clean_autonomous_name(name)
                if dry_run:
                    self.stdout.write(f"  [dry-run] DISTRICT autonome : {code} | {short} ← « {name} »")
                    continue
                District.objects.update_or_create(
                    code=code,
                    defaults={"name": short, "is_autonomous": True, "geom": mpoly},
                )
                autonomes_imported += 1
                continue

            # Sinon : c'est une région — trouver son district parent
            district_name = region_to_district.get(normalize_name(name))
            if not district_name:
                unmatched_regions.append((code, name))
                continue

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] RÉGION : {code} | {name} → district {district_name}"
                )
                continue
            district = District.objects.get(code=district_code(district_name))
            Region.objects.update_or_create(
                code=code,
                defaults={"name": name, "district": district, "geom": mpoly},
            )
            regions_imported += 1

        if unmatched_regions:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ {len(unmatched_regions)} région(s) sans district parent (mapping à compléter) :"
            ))
            for code, name in unmatched_regions:
                self.stdout.write(f"    - {code} | {name}")

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\n  Dry-run : aucune écriture en base. "
                "Re-lance sans --dry-run pour appliquer."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"\n  ✓ {autonomes_imported} districts autonomes + "
            f"{regions_imported} régions importés depuis HDX."
        ))

        # 3) Reconstruction de la géom des districts non-autonomes
        self._rebuild_district_geoms()

    # ── Reconstruction géom districts non-autonomes ────────────────────────

    def _rebuild_district_geoms(self):
        """
        Calcule la géom de chaque district non-autonome comme l'union spatiale
        de ses régions enfantes. Effectué en SQL via ST_Union (Django aggregate).
        """
        self.stdout.write(self.style.HTTP_INFO(
            "\n=== Reconstruction géom des 12 districts non-autonomes (ST_Union) ==="
        ))
        rebuilt, skipped = 0, 0
        for d in District.objects.filter(is_autonomous=False):
            agg = d.regions.aggregate(u=GeomUnion("geom"))
            union = agg["u"]
            if union is None:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ {d.name} : aucune région rattachée, géom non recalculée."
                ))
                skipped += 1
                continue

            if isinstance(union, Polygon):
                union = MultiPolygon(union, srid=4326)
            elif union.geom_type != "MultiPolygon":
                self.stderr.write(self.style.ERROR(
                    f"  {d.name} : type d'union inattendu ({union.geom_type}), skip."
                ))
                skipped += 1
                continue

            d.geom = union
            d.save(update_fields=["geom"])
            rebuilt += 1
            self.stdout.write(f"  ✓ {d.name} ({d.regions.count()} régions)")

        self.stdout.write(self.style.SUCCESS(
            f"  → {rebuilt} géoms reconstituées, {skipped} skip."
        ))

    # ── Import ADM2 — Départements ─────────────────────────────────────────

    def _import_adm2(self, sources, dry_run):
        """
        Importe les 108 départements depuis le layer ADM2 HDX.

        Chaque feature porte `adm1_pcode` qui identifie le parent (région ou
        district autonome). On résout :
          - Si `adm1_pcode` correspond à un `Region.code` → département
            classique : `region` rempli, `district = region.district`.
          - Si `adm1_pcode` correspond à un `District.code` (autonome) →
            département autonome : `region = None`, `district` rempli direct.

        Idempotent via update_or_create par `adm2_pcode`.
        """
        found = self._find_layer(sources, 2)
        if not found:
            self.stderr.write(self.style.ERROR("Aucun layer ADM2 trouvé."))
            return
        label, layer = found

        self.stdout.write(self.style.HTTP_INFO(f"\n=== ADM2 — {label} ==="))
        fields = list(layer.fields)

        name_field   = first_match(fields, ADM2_NAME_CANDIDATES)
        pcode_field  = first_match(fields, ADM2_PCODE_CANDIDATES)
        parent_field = first_match(fields, ADM2_PARENT_CANDIDATES)
        if not (name_field and pcode_field and parent_field):
            self.stderr.write(self.style.ERROR(
                f"Colonnes attendues introuvables dans {fields}"
            ))
            return
        self.stdout.write(
            f"  Mapping colonnes : name={name_field}, code={pcode_field}, "
            f"parent={parent_field}"
        )

        # Indexes en mémoire pour résolution rapide du parent
        region_by_code   = {r.code: r for r in Region.objects.all()}
        district_by_code = {d.code: d for d in District.objects.all()}

        classic, autonomous, unmatched = 0, 0, []
        for feat in layer:
            name        = str(feat.get(name_field) or "").strip()
            code        = str(feat.get(pcode_field) or "").strip()
            parent_code = str(feat.get(parent_field) or "").strip()
            geom        = self._reproject_4326(feat.geom)
            mpoly       = to_multipolygon(geom.geos if geom else None)

            if not (name and code and parent_code and mpoly):
                continue

            region   = region_by_code.get(parent_code)
            district = None
            if region:
                district = region.district
            else:
                district = district_by_code.get(parent_code)
                if district is None or not district.is_autonomous:
                    unmatched.append((code, name, parent_code))
                    continue

            if dry_run:
                tag = "autonome" if region is None else f"région {region.name}"
                self.stdout.write(
                    f"  [dry-run] DÉPARTEMENT : {code} | {name} → {tag}"
                )
                continue

            Departement.objects.update_or_create(
                code=code,
                defaults={
                    "name":     name,
                    "district": district,
                    "region":   region,  # None pour les autonomes
                    "geom":     mpoly,
                },
            )
            if region is None:
                autonomous += 1
            else:
                classic += 1

        if unmatched:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ {len(unmatched)} département(s) sans parent résolu :"
            ))
            for code, name, parent in unmatched:
                self.stdout.write(f"    - {code} | {name} ← parent={parent}")

        if dry_run:
            return

        self.stdout.write(self.style.SUCCESS(
            f"  ✓ {classic} départements classiques + {autonomous} départements "
            f"autonomes importés depuis HDX ADM2."
        ))

    # ── Import ADM3 — Sous-préfectures ────────────────────────────────────

    def _import_adm3(self, sources, dry_run):
        """
        Importe les 510 sous-préfectures depuis le layer ADM3 HDX.

        Chaque feature porte `adm2_pcode` qui identifie le département parent.
        Le rattachement est strict : pas de cas particulier, les SP des
        districts autonomes pointent simplement sur leur département autonome
        (lui-même importé en ADM2).

        Idempotent via update_or_create par `adm3_pcode`.
        """
        found = self._find_layer(sources, 3)
        if not found:
            self.stderr.write(self.style.ERROR("Aucun layer ADM3 trouvé."))
            return
        label, layer = found

        self.stdout.write(self.style.HTTP_INFO(f"\n=== ADM3 — {label} ==="))
        fields = list(layer.fields)

        name_field   = first_match(fields, ADM3_NAME_CANDIDATES)
        pcode_field  = first_match(fields, ADM3_PCODE_CANDIDATES)
        parent_field = first_match(fields, ADM3_PARENT_CANDIDATES)
        if not (name_field and pcode_field and parent_field):
            self.stderr.write(self.style.ERROR(
                f"Colonnes attendues introuvables dans {fields}"
            ))
            return
        self.stdout.write(
            f"  Mapping colonnes : name={name_field}, code={pcode_field}, "
            f"parent={parent_field}"
        )

        departement_by_code = {d.code: d for d in Departement.objects.all()}

        imported, unmatched = 0, []
        for feat in layer:
            name        = str(feat.get(name_field) or "").strip()
            code        = str(feat.get(pcode_field) or "").strip()
            parent_code = str(feat.get(parent_field) or "").strip()
            geom        = self._reproject_4326(feat.geom)
            mpoly       = to_multipolygon(geom.geos if geom else None)

            if not (name and code and parent_code and mpoly):
                continue

            departement = departement_by_code.get(parent_code)
            if departement is None:
                unmatched.append((code, name, parent_code))
                continue

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] SOUS-PRÉFECTURE : {code} | {name} → "
                    f"département {departement.name}"
                )
                continue

            SousPrefecture.objects.update_or_create(
                code=code,
                defaults={
                    "name":        name,
                    "departement": departement,
                    "geom":        mpoly,
                },
            )
            imported += 1

        if unmatched:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ {len(unmatched)} sous-préfecture(s) sans département "
                f"parent (ADM2 doit être importé avant ADM3) :"
            ))
            for code, name, parent in unmatched[:10]:
                self.stdout.write(f"    - {code} | {name} ← parent={parent}")
            if len(unmatched) > 10:
                self.stdout.write(f"    … et {len(unmatched) - 10} autres.")

        if dry_run:
            return

        self.stdout.write(self.style.SUCCESS(
            f"  ✓ {imported} sous-préfectures importées depuis HDX ADM3."
        ))

    # ── Téléchargement (cas --zip-url) ────────────────────────────────────

    def _download(self, url):
        self.stdout.write(self.style.HTTP_INFO(f"Téléchargement {url}..."))
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        with requests.get(url, stream=True, timeout=180,
                          headers={"User-Agent": "geodashboard/1.0"}) as r:
            r.raise_for_status()
            total = 0
            for chunk in r.iter_content(chunk_size=65536):
                tmp.write(chunk)
                total += len(chunk)
        tmp.close()
        self.stdout.write(f"  → {tmp.name} ({total / 1_048_576:.1f} MB)")
        return tmp.name

    # ── Sources & layers ──────────────────────────────────────────────────

    def _discover_sources(self, extract_dir):
        """
        Découvre les sources GIS. Supporte :
          - Shapefile (.shp) en lecture directe.
          - File Geodatabase (.gdb) convertie en .shp via ogr2ogr (le driver
            OpenFileGDB de GDAL peut renvoyer un schéma vide sur certains
            .gdb HDX — passer par ogr2ogr en mode traduction règle ça).
        """
        for gdb in Path(extract_dir).rglob("*.gdb"):
            if gdb.is_dir():
                self._convert_gdb_to_shp(gdb, extract_dir)

        sources = []
        for shp in sorted(Path(extract_dir).rglob("*.shp")):
            try:
                ds = DataSource(str(shp))
                sources.append((shp.name, ds[0]))
            except Exception as exc:
                self.stderr.write(self.style.WARNING(
                    f"  Lecture impossible de {shp.name}: {exc}"
                ))
        return sources

    def _convert_gdb_to_shp(self, gdb_path, extract_dir):
        out_dir = Path(extract_dir) / "_converted_shp"
        out_dir.mkdir(exist_ok=True)
        self.stdout.write(self.style.HTTP_INFO(
            f"Conversion {gdb_path.name} → SHP via ogr2ogr..."
        ))
        result = subprocess.run(
            ["ogr2ogr", "-f", "ESRI Shapefile",
             "-overwrite", "-lco", "ENCODING=UTF-8",
             str(out_dir), str(gdb_path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            self.stderr.write(self.style.ERROR(
                f"  ogr2ogr a échoué (code {result.returncode}) :\n"
                f"  stderr: {result.stderr[:500]}"
            ))

    # Layers à ignorer pour cibler les sources "originales".
    _LAYER_EXCLUDE_TOKENS = {"em", "centroid", "centroids", "capital", "capitals",
                             "line", "lines", "point", "points"}

    def _find_layer(self, sources, level):
        """
        Trouve le layer dont les tokens contiennent `admin{level}` ou `adm{level}`,
        en excluant les variantes (_em, centroids, lines, capitals…).
        """
        targets = {f"admin{level}", f"adm{level}"}
        for label, layer in sources:
            tokens = set(re.split(r"[._:\-/ ]+", label.lower()))
            if targets & tokens and not (tokens & self._LAYER_EXCLUDE_TOKENS):
                return (label, layer)
        return None

    def _reproject_4326(self, gdal_geom):
        """Reprojette en WGS84 si nécessaire. Renvoie un objet GDAL."""
        if gdal_geom is None:
            return None
        if gdal_geom.srid and gdal_geom.srid != 4326:
            return gdal_geom.transform(4326, clone=True)
        return gdal_geom
