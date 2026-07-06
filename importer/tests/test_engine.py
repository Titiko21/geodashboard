"""
Tests de l'importeur générique.

Deux volets :
  - logique pure (mapping, résolution de colonnes, registre) — sans DB ;
  - import de bout en bout du GeoJSON communes livré, via le moteur
    générique (le même chemin que `import_communes` et `import_layer`).
"""
import json

import pytest

from admin_divisions.communes import DEFAULT_FILE
from admin_divisions.models import Commune, District, Ville
from importer import MAPPINGS_DIR
from importer.engine import (
    ImporterError,
    load_mapping,
    resolve_column,
    run_import,
)
from importer.registry import available_targets, get_target

COMMUNES_MAPPING = MAPPINGS_DIR / "communes_grand_abidjan.json"


# ── Registre ────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_commune_target_registered(self):
        assert "commune" in available_targets()

    def test_unknown_target_raises_with_available_list(self):
        with pytest.raises(KeyError, match="commune"):
            get_target("nexiste-pas")


# ── load_mapping ────────────────────────────────────────────────────────────

class TestLoadMapping:
    def test_shipped_communes_mapping_is_valid(self):
        m = load_mapping(COMMUNES_MAPPING)
        assert m.target == "commune"
        assert m.columns["name"] == ("NOMS", "NOM", "name", "NAME")

    def test_missing_file(self):
        with pytest.raises(ImporterError, match="introuvable"):
            load_mapping("/nulle/part/mapping.json")

    def test_missing_target(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"columns": {"name": "NOM"}}), encoding="utf-8")
        with pytest.raises(ImporterError, match="target"):
            load_mapping(p)

    def test_missing_columns(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"target": "commune"}), encoding="utf-8")
        with pytest.raises(ImporterError, match="columns"):
            load_mapping(p)

    def test_single_column_normalized_to_tuple(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(
            json.dumps({"target": "commune", "columns": {"name": "NOM"}}),
            encoding="utf-8",
        )
        assert load_mapping(p).columns["name"] == ("NOM",)

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text("{pas du json", encoding="utf-8")
        with pytest.raises(ImporterError, match="illisible"):
            load_mapping(p)


# ── resolve_column ──────────────────────────────────────────────────────────

class TestResolveColumn:
    def test_exact(self):
        assert resolve_column(["NOMS", "geom"], ("NOMS",)) == "NOMS"

    def test_case_insensitive(self):
        assert resolve_column(["noms"], ("NOMS",)) == "noms"

    def test_priority_order(self):
        assert resolve_column(["NOM", "NOMS"], ("NOMS", "NOM")) == "NOMS"

    def test_no_match(self):
        assert resolve_column(["autre"], ("NOMS", "NOM")) is None


# ── Import de bout en bout (GeoJSON communes livré) ─────────────────────────

pytestmark_db = pytest.mark.django_db


@pytest.mark.django_db
class TestEndToEndCommunes:
    def test_dry_run_writes_nothing(self):
        report = run_import(DEFAULT_FILE, COMMUNES_MAPPING, dry_run=True)
        assert report.created == 14
        assert report.updated == 0
        assert Commune.objects.count() == 0

    def test_import_creates_14_communes_and_nothing_else(self):
        report = run_import(DEFAULT_FILE, COMMUNES_MAPPING)
        assert report.created == 14
        assert report.skipped == []
        assert Commune.objects.count() == 14
        # Aucune entité hiérarchique créée (décision « communes seules »).
        assert Ville.objects.count() == 0
        assert District.objects.count() == 0
        # FK hiérarchiques toutes NULL.
        assert not Commune.objects.exclude(district=None, ville=None).exists()

    def test_reimport_is_idempotent(self):
        run_import(DEFAULT_FILE, COMMUNES_MAPPING)
        report = run_import(DEFAULT_FILE, COMMUNES_MAPPING)
        assert report.created == 0
        assert report.updated == 14
        assert Commune.objects.count() == 14

    def test_canonical_names_applied(self):
        run_import(DEFAULT_FILE, COMMUNES_MAPPING)
        assert Commune.objects.filter(name="Port-Bouët").exists()
        assert Commune.objects.filter(code="CIV-COM-COCODY").exists()

    def test_geometries_are_multipolygons_4326(self):
        run_import(DEFAULT_FILE, COMMUNES_MAPPING)
        for c in Commune.objects.all():
            assert c.geom is not None
            assert c.geom.geom_type == "MultiPolygon"
            assert c.geom.srid == 4326

    def test_unknown_commune_is_skipped(self, tmp_path):
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"NOMS": "Bouaké"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-5.0, 7.6], [-5.1, 7.6],
                                     [-5.1, 7.7], [-5.0, 7.7], [-5.0, 7.6]]],
                },
            }],
        }
        f = tmp_path / "inconnue.geojson"
        f.write_text(json.dumps(geojson), encoding="utf-8")
        report = run_import(f, COMMUNES_MAPPING)
        assert report.created == 0
        assert len(report.skipped) == 1
        assert "non reconnue" in report.skipped[0][1]

    def test_missing_name_column_raises(self, tmp_path):
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"AUTRE": "x"},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }],
        }
        f = tmp_path / "sans_nom.geojson"
        f.write_text(json.dumps(geojson), encoding="utf-8")
        with pytest.raises(ImporterError, match="Aucune colonne"):
            run_import(f, COMMUNES_MAPPING)
