"""
Tests de la commande import_communes — helpers purs + intégrité du fichier
de données livré (sans I/O DB).

Couvre :
  - commune_code : génération de code stable, préfixe, longueur
  - canonical_name : remise au propre des noms (casse/accents)
  - is_known_commune : ensemble fermé des 14 communes du fichier livré
  - cohérence du GeoJSON livré (14 communes, toutes reconnues, codes uniques)
"""
import json

from admin_divisions.management.commands.import_communes import (
    DEFAULT_FILE,
    canonical_name,
    commune_code,
    is_known_commune,
)

# Longueur max du champ Commune.code (cf. models.Commune).
CODE_MAX_LEN = 30


# ── commune_code ───────────────────────────────────────────────────────────

class TestCommuneCode:
    def test_simple_name(self):
        assert commune_code("Cocody") == "CIV-COM-COCODY"

    def test_compound_name(self):
        assert commune_code("Grand-Bassam") == "CIV-COM-GRAND-BASSAM"
        assert commune_code("Port-Bouët")   == "CIV-COM-PORT-BOUET"

    def test_accent_insensitive(self):
        # « Port-bouet » (source) et « Port-Bouët » (propre) → même code
        assert commune_code("Port-bouet") == commune_code("Port-Bouët")

    def test_prefix_distinct_from_district(self):
        assert commune_code("Cocody").startswith("CIV-COM-")

    def test_within_max_length(self):
        for name in ("Bingerville", "Treichville", "Grand-Bassam"):
            assert len(commune_code(name)) <= CODE_MAX_LEN


# ── canonical_name ─────────────────────────────────────────────────────────

class TestCanonicalName:
    def test_fixes_casing_and_accents(self):
        assert canonical_name("Port-bouet") == "Port-Bouët"

    def test_known_passthrough(self):
        assert canonical_name("Cocody") == "Cocody"

    def test_unknown_returned_unchanged(self):
        assert canonical_name("Ville-Inconnue") == "Ville-Inconnue"


# ── is_known_commune ───────────────────────────────────────────────────────

class TestIsKnownCommune:
    def test_known(self):
        assert is_known_commune("Cocody") is True
        assert is_known_commune("Grand-Bassam") is True

    def test_accent_insensitive(self):
        # sans accents (format possible d'un autre export)
        assert is_known_commune("Attecoube") is True
        assert is_known_commune("Port-bouet") is True

    def test_unknown(self):
        assert is_known_commune("Bouaké") is False
        assert is_known_commune("") is False


# ── Intégrité du fichier livré ─────────────────────────────────────────────

class TestShippedDataFile:
    def _features(self):
        data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
        return data["features"]

    def test_file_exists(self):
        assert DEFAULT_FILE.exists()

    def test_has_14_communes(self):
        assert len(self._features()) == 14

    def test_every_commune_is_known(self):
        unknown = [
            f["properties"].get("NOMS")
            for f in self._features()
            if not is_known_commune(f["properties"].get("NOMS"))
        ]
        assert unknown == []

    def test_codes_are_unique_and_within_length(self):
        codes = [commune_code(f["properties"]["NOMS"]) for f in self._features()]
        assert len(set(codes)) == len(codes)
        assert all(len(c) <= CODE_MAX_LEN for c in codes)

    def test_all_geometries_are_polygonal(self):
        for f in self._features():
            assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
