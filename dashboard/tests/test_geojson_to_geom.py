"""
Tests du helper `geojson_to_geom` (dashboard/_geo_utils.py).

Couvre les cas qui ont fait planter la migration 0006 :
  - Polygon ring < 4 points → None
  - Polygon non fermé → auto-clos
  - LineString < 2 points → None
  - GeoJSON vide / malformé → None
"""
import pytest

from dashboard._geo_utils import geojson_to_geom


pytestmark = pytest.mark.django_db


class TestLineString:
    def test_valid(self):
        g = geojson_to_geom({
            "type": "LineString",
            "coordinates": [[-4.0, 5.3], [-4.1, 5.4]],
        })
        assert g is not None
        assert g.geom_type == "LineString"
        assert g.srid == 4326

    def test_too_few_points(self):
        g = geojson_to_geom({
            "type": "LineString",
            "coordinates": [[-4.0, 5.3]],  # 1 point seul
        })
        assert g is None


class TestPolygon:
    def test_valid_closed(self):
        g = geojson_to_geom({
            "type": "Polygon",
            "coordinates": [[
                [-4.0, 5.3], [-4.1, 5.3], [-4.1, 5.4], [-4.0, 5.4], [-4.0, 5.3]
            ]],
        })
        assert g is not None
        assert g.geom_type == "Polygon"
        assert g.srid == 4326

    def test_unclosed_polygon_is_auto_closed(self):
        """Le helper ferme automatiquement les rings non fermés."""
        g = geojson_to_geom({
            "type": "Polygon",
            "coordinates": [[
                [-4.0, 5.3], [-4.1, 5.3], [-4.1, 5.4], [-4.0, 5.4]  # 4 pts mais non fermé
            ]],
        })
        assert g is not None
        assert g.geom_type == "Polygon"

    def test_ring_too_few_points(self):
        """Bug rencontré dans la migration 0006 : ring < 4 points doit
        retourner None (et pas planter)."""
        g = geojson_to_geom({
            "type": "Polygon",
            "coordinates": [[[-4.0, 5.3], [-4.1, 5.3], [-4.0, 5.3]]],  # 3 pts seulement
        })
        assert g is None


class TestMalformed:
    def test_empty_dict(self):
        assert geojson_to_geom({}) is None

    def test_none(self):
        assert geojson_to_geom(None) is None

    def test_missing_coordinates(self):
        assert geojson_to_geom({"type": "LineString"}) is None

    def test_missing_type(self):
        assert geojson_to_geom({"coordinates": [[1, 2], [3, 4]]}) is None

    def test_non_dict(self):
        assert geojson_to_geom("not a dict") is None
        assert geojson_to_geom([1, 2, 3]) is None
