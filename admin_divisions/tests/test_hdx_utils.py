"""
Tests des helpers d'import HDX (logique pure, sans I/O DB).

Couvre :
  - first_match : matching de colonnes insensible à la casse
  - is_autonomous_name : détection des districts autonomes (Abidjan, Yamoussoukro)
  - normalize_name : normalisation pour matching HDX ↔ mapping interne
  - clean_autonomous_name : extraction du nom court depuis libellé HDX
  - region_to_district_map : inversion du mapping
  - district_code : génération de code stable
  - to_multipolygon : Polygon → MultiPolygon, idempotence, rejets
  - cohérence interne du mapping DISTRICT_REGIONS (31 régions distinctes)
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from admin_divisions._hdx_utils import (
    DISTRICT_REGIONS,
    clean_autonomous_name,
    district_code,
    first_match,
    is_autonomous_name,
    normalize_name,
    region_to_district_map,
    to_multipolygon,
)


# ── first_match ────────────────────────────────────────────────────────────

class TestFirstMatch:
    def test_exact_match(self):
        assert first_match(["adm1_name", "geom"], ("adm1_name",)) == "adm1_name"

    def test_case_insensitive(self):
        assert first_match(["adm1_name"], ("ADM1_NAME",)) == "adm1_name"

    def test_priority_order(self):
        fields = ["adm1_name", "ADM1_FR"]
        assert first_match(fields, ("adm1_name", "ADM1_FR")) == "adm1_name"

    def test_no_match_returns_none(self):
        assert first_match(["name", "geom"], ("ADM1_PCODE",)) is None


# ── normalize_name ─────────────────────────────────────────────────────────

class TestNormalizeName:
    def test_strips_accents(self):
        assert normalize_name("Agnéby-Tiassa") == "agneby tiassa"
        assert normalize_name("Gôh-Djiboua")   == "goh djiboua"

    def test_hdx_format_matches_internal(self):
        """Le but du normalize : HDX (sans accents) et notre mapping (avec)
        donnent la même chaîne normalisée."""
        assert normalize_name("Agneby-Tiassa") == normalize_name("Agnéby-Tiassa")
        assert normalize_name("Sud-Comoe")     == normalize_name("Sud-Comoé")
        assert normalize_name("Indenie-Djuablin") == normalize_name("Indénié-Djuablin")

    def test_apostrophe_removed(self):
        assert normalize_name("N'Zi") == "nzi"

    def test_dash_and_space_equivalent(self):
        assert normalize_name("Grands-Ponts") == normalize_name("Grands Ponts")
        assert normalize_name("San-Pédro")    == normalize_name("San Pedro")

    def test_empty_or_none(self):
        assert normalize_name(None) == ""
        assert normalize_name("")   == ""


# ── is_autonomous_name ─────────────────────────────────────────────────────

class TestIsAutonomousName:
    def test_short_form(self):
        assert is_autonomous_name("Abidjan")      is True
        assert is_autonomous_name("Yamoussoukro") is True

    def test_hdx_full_form(self):
        """HDX écrit "District Autonome D'Abidjan" — doit matcher."""
        assert is_autonomous_name("District Autonome D'Abidjan")    is True
        assert is_autonomous_name("District Autonome De Yamoussoukro") is True

    def test_other_districts_not_autonomous(self):
        assert is_autonomous_name("Lagunes")   is False
        assert is_autonomous_name("Zanzan")    is False
        assert is_autonomous_name("Cavally")   is False

    def test_none_or_empty(self):
        assert is_autonomous_name(None) is False
        assert is_autonomous_name("")   is False


# ── clean_autonomous_name ──────────────────────────────────────────────────

class TestCleanAutonomousName:
    def test_abidjan_long_form(self):
        assert clean_autonomous_name("District Autonome D'Abidjan") == "Abidjan"

    def test_yamoussoukro_long_form(self):
        assert clean_autonomous_name("District Autonome De Yamoussoukro") == "Yamoussoukro"

    def test_already_short(self):
        assert clean_autonomous_name("Abidjan")      == "Abidjan"
        assert clean_autonomous_name("Yamoussoukro") == "Yamoussoukro"

    def test_unknown_unchanged(self):
        assert clean_autonomous_name("Lagunes") == "Lagunes"


# ── region_to_district_map ─────────────────────────────────────────────────

class TestRegionToDistrictMap:
    def test_known_region_resolves_to_district(self):
        m = region_to_district_map()
        # accent dans le mapping interne, sans accent en input — doit matcher
        assert m[normalize_name("Agneby-Tiassa")] == "Lagunes"
        assert m[normalize_name("Gontougo")]      == "Zanzan"
        assert m[normalize_name("Hambol")]        == "Vallée du Bandama"
        assert m[normalize_name("San Pedro")]     == "Bas-Sassandra"

    def test_total_region_count_is_31(self):
        """Sanity-check : 31 régions distinctes dans le mapping (réalité CI)."""
        m = region_to_district_map()
        assert len(m) == 31

    def test_total_district_count_is_12(self):
        """Sanity-check : 12 districts non-autonomes (réalité CI)."""
        assert len(DISTRICT_REGIONS) == 12


# ── district_code ──────────────────────────────────────────────────────────

class TestDistrictCode:
    def test_simple_name(self):
        assert district_code("Lagunes") == "CIV-DIS-LAGUNES"

    def test_compound_name(self):
        assert district_code("Bas-Sassandra")      == "CIV-DIS-BAS-SASSANDRA"
        assert district_code("Vallée du Bandama")  == "CIV-DIS-VALLEE-DU-BANDAMA"
        assert district_code("Gôh-Djiboua")        == "CIV-DIS-GOH-DJIBOUA"

    def test_no_collision_with_hdx_codes(self):
        """Les codes HDX ADM1 sont CI01-CI33 — nos codes générés ne doivent
        jamais avoir cette forme."""
        for d in DISTRICT_REGIONS:
            c = district_code(d)
            assert c.startswith("CIV-DIS-")
            assert not c.startswith("CI0")


# ── to_multipolygon (inchangé) ─────────────────────────────────────────────

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
