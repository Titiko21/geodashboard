"""
Tests du travail de suite déclaré par une cible (`TargetSpec.after_import`).

Enjeu métier : un relevé d'inondation importé impose un PLANCHER au score de
sa commune (scoring v3). Sans recalcul, la carte affiche des points que les
scores communaux ignorent — deux lectures contradictoires à l'écran.

Le calcul réel interroge Earth Engine (~15 s par commune) : les tests
remplacent le recalcul par une sonde, et vérifient QUI serait recalculé et
DANS QUELS CAS — pas les valeurs de score (couvertes par flood/tests/).
"""
import json

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from admin_divisions.models import Commune
from importer.engine import run_import
from importer.registry import get_target

pytestmark = pytest.mark.django_db


MAPPING = {
    "target": "flood_event",
    "columns": {"name": ["NOM"], "date": ["DATE"], "source": ["SOURCE"]},
}


def _carre(x, y, cote=0.1):
    return MultiPolygon(Polygon.from_bbox((x, y, x + cote, y + cote)))


def _fichier(tmp_path, points):
    """GeoJSON de relevés ponctuels : [(nom, lng, lat), …]."""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NOM": nom, "DATE": "2026-07-20", "SOURCE": "test"},
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
            }
            for nom, lng, lat in points
        ],
    }
    p = tmp_path / "releves.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")
    m = tmp_path / "mapping.json"
    m.write_text(json.dumps(MAPPING), encoding="utf-8")
    return p, m


@pytest.fixture
def deux_communes(db):
    a = Commune.objects.create(code="TEST-A", name="Test A", geom=_carre(-4.0, 5.3))
    b = Commune.objects.create(code="TEST-B", name="Test B", geom=_carre(-3.0, 5.3))
    return a, b


@pytest.fixture
def sonde(monkeypatch):
    """Remplace le recalcul réel par une sonde qui note les communes visées."""
    vues = []

    def _faux_recompute(queryset=None, log=lambda m: None):
        noms = sorted(queryset.values_list("name", flat=True))
        vues.extend(noms)
        return len(noms), []

    monkeypatch.setattr("flood.susceptibility.recompute_communes", _faux_recompute)
    return vues


class TestCibleFloodEvent:
    def test_la_cible_declare_un_travail_de_suite(self):
        assert get_target("flood_event").after_import is not None

    def test_la_cible_commune_nen_declare_pas(self):
        assert get_target("commune").after_import is None


class TestRecalculApresImport:
    def test_recalcule_uniquement_les_communes_recoupees(
        self, tmp_path, deux_communes, sonde
    ):
        # Un seul relevé, tombant dans Test A.
        fichier, mapping = _fichier(tmp_path, [("Zone A1", -3.95, 5.35)])
        report = run_import(file_path=str(fichier), mapping_path=str(mapping))

        assert report.created == 1
        # Test B n'est pas touchée : elle ne doit pas coûter 15 s de GEE.
        assert sonde == ["Test A"]

    def test_plusieurs_releves_dans_la_meme_commune_ne_la_recalculent_quune_fois(
        self, tmp_path, deux_communes, sonde
    ):
        fichier, mapping = _fichier(
            tmp_path,
            [("Zone A1", -3.95, 5.35), ("Zone A2", -3.94, 5.36), ("Zone A3", -3.93, 5.34)],
        )
        run_import(file_path=str(fichier), mapping_path=str(mapping))
        assert sonde == ["Test A"]

    def test_releves_dans_deux_communes_les_recalculent_toutes_les_deux(
        self, tmp_path, deux_communes, sonde
    ):
        fichier, mapping = _fichier(
            tmp_path, [("Zone A1", -3.95, 5.35), ("Zone B1", -2.95, 5.35)]
        )
        run_import(file_path=str(fichier), mapping_path=str(mapping))
        assert sonde == ["Test A", "Test B"]

    def test_releve_hors_de_toute_commune_ne_recalcule_rien(
        self, tmp_path, deux_communes, sonde
    ):
        # Au large, hors des deux emprises.
        fichier, mapping = _fichier(tmp_path, [("En mer", -10.0, 2.0)])
        report = run_import(file_path=str(fichier), mapping_path=str(mapping))
        assert report.created == 1
        assert sonde == []


class TestGardeFous:
    def test_dry_run_ne_declenche_aucun_recalcul(
        self, tmp_path, deux_communes, sonde
    ):
        fichier, mapping = _fichier(tmp_path, [("Zone A1", -3.95, 5.35)])
        report = run_import(
            file_path=str(fichier), mapping_path=str(mapping), dry_run=True
        )
        assert report.created == 1      # compté en aperçu
        assert report.written_keys == []
        assert sonde == []

    def test_run_hooks_false_ne_declenche_aucun_recalcul(
        self, tmp_path, deux_communes, sonde
    ):
        """Chemin de la vue admin : l'import écrit, mais sans travail de suite."""
        fichier, mapping = _fichier(tmp_path, [("Zone A1", -3.95, 5.35)])
        report = run_import(
            file_path=str(fichier), mapping_path=str(mapping), run_hooks=False
        )
        assert report.created == 1
        assert report.written_keys           # les données SONT écrites
        assert sonde == []                   # mais rien n'est recalculé

    def test_un_echec_de_recalcul_ne_perd_pas_les_donnees_importees(
        self, tmp_path, deux_communes, monkeypatch
    ):
        """Le crochet tourne hors transaction : l'import reste acquis."""
        def _explose(queryset=None, log=lambda m: None):
            raise RuntimeError("GEE indisponible")

        monkeypatch.setattr("flood.susceptibility.recompute_communes", _explose)
        fichier, mapping = _fichier(tmp_path, [("Zone A1", -3.95, 5.35)])

        from flood.models import FloodEvent
        with pytest.raises(RuntimeError):
            run_import(file_path=str(fichier), mapping_path=str(mapping))
        assert FloodEvent.objects.filter(name="Zone A1").exists()


class TestMappingLivre:
    def test_le_mapping_zones_inondees_est_valide(self):
        from importer import MAPPINGS_DIR
        from importer.engine import load_mapping

        m = load_mapping(MAPPINGS_DIR / "zones_inondees.json")
        assert m.target == "flood_event"
        assert "NOM" in m.columns["name"]
