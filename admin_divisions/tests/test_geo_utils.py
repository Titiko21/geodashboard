"""
Tests des helpers géométriques / de nommage (logique pure, sans I/O DB).

Couvre :
  - normalize_name : normalisation pour matching (accents, apostrophes, tirets)
  - district_code : génération de code stable
  - to_multipolygon : Polygon → MultiPolygon, idempotence, rejets
"""
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from admin_divisions._geo_utils import district_code, normalize_name, to_multipolygon


# ── normalize_name ─────────────────────────────────────────────────────────

class TestNormalizeName:
    def test_strips_accents(self):
        assert normalize_name("Agnéby-Tiassa") == "agneby tiassa"
        assert normalize_name("Gôh-Djiboua")   == "goh djiboua"

    def test_accented_and_plain_match(self):
        """Accents présents ou non → même chaîne normalisée."""
        assert normalize_name("Agneby-Tiassa") == normalize_name("Agnéby-Tiassa")
        assert normalize_name("Sud-Comoe")     == normalize_name("Sud-Comoé")

    def test_apostrophe_removed(self):
        assert normalize_name("N'Zi") == "nzi"

    def test_dash_and_space_equivalent(self):
        assert normalize_name("Grands-Ponts") == normalize_name("Grands Ponts")
        assert normalize_name("San-Pédro")    == normalize_name("San Pedro")

    def test_empty_or_none(self):
        assert normalize_name(None) == ""
        assert normalize_name("")   == ""


# ── district_code ──────────────────────────────────────────────────────────

class TestDistrictCode:
    def test_simple_name(self):
        assert district_code("Lagunes") == "CIV-DIS-LAGUNES"

    def test_compound_name(self):
        assert district_code("Bas-Sassandra")      == "CIV-DIS-BAS-SASSANDRA"
        assert district_code("Vallée du Bandama")  == "CIV-DIS-VALLEE-DU-BANDAMA"
        assert district_code("Gôh-Djiboua")        == "CIV-DIS-GOH-DJIBOUA"


# ── to_multipolygon ────────────────────────────────────────────────────────

class TestToMultipolygon:
    def _polygon(self):
        return Polygon((
            (0, 0), (1, 0), (1, 1), (0, 1), (0, 0),
        ), srid=4326)

    def test_polygon_wrapped(self):
        result = to_multipolygon(self._polygon())
        assert result.geom_type == "MultiPolygon"
        assert result.srid == 4326

    def test_multipolygon_passes_through(self):
        mp = MultiPolygon(self._polygon(), srid=4326)
        assert to_multipolygon(mp) is mp

    def test_point_rejected(self):
        assert to_multipolygon(Point(0, 0, srid=4326)) is None

    def test_none_rejected(self):
        assert to_multipolygon(None) is None
