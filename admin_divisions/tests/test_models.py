"""
Tests de structure des modèles admin_divisions.

Couvre :
  - Création des 5 modèles avec leurs FK
  - Cas du district autonome (Commune sans région/département/sous-préf)
  - Cas standard (Commune dans la chaîne déconcentrée complète)
  - Round-trip PostGIS sur les MultiPolygon
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from admin_divisions.models import (
    Commune,
    Departement,
    District,
    Region,
    SousPrefecture,
)


pytestmark = pytest.mark.django_db


def _square(lng, lat, size=0.1):
    """Helper : crée un MultiPolygon carré pour les tests géo."""
    p = Polygon((
        (lng, lat),
        (lng + size, lat),
        (lng + size, lat + size),
        (lng, lat + size),
        (lng, lat),
    ))
    return MultiPolygon(p, srid=4326)


class TestStandardChain:
    """Cas Bondoukou — chaîne complète District/Région/Département/Sous-préf/Commune."""

    def test_full_chain_creation(self):
        district = District.objects.create(code="ZAN", name="Zanzan")
        region   = Region.objects.create(code="GTG", name="Gontougo", district=district)
        dept     = Departement.objects.create(code="BDK", name="Bondoukou", region=region)
        sp       = SousPrefecture.objects.create(code="BDK-CTR", name="Bondoukou-Centre", departement=dept)
        commune  = Commune.objects.create(
            code="C-BDK", name="Bondoukou",
            district=district, region=region, departement=dept, sous_prefecture=sp,
        )

        assert commune.district == district
        assert commune.region == region
        assert commune.departement == dept
        assert commune.sous_prefecture == sp
        # Vérif relations inverses
        assert commune in district.communes.all()
        assert commune in region.communes.all()


class TestAutonomousDistrict:
    """Cas Cocody/Plateau/Yopougon — district autonome, niveaux intermédiaires NULL."""

    def test_autonomous_district_commune_minimal(self):
        district = District.objects.create(code="ABJ", name="Abidjan", is_autonomous=True)
        cocody   = Commune.objects.create(
            code="C-COC", name="Cocody", district=district,
            # region, departement, sous_prefecture restent NULL
        )

        assert cocody.region is None
        assert cocody.departement is None
        assert cocody.sous_prefecture is None
        assert cocody.district.is_autonomous is True


class TestGeometry:
    """Round-trip PostGIS — on s'assure que les MultiPolygon survivent au save/reload."""

    def test_district_multipolygon_roundtrip(self):
        district = District.objects.create(
            code="LAG", name="Lagunes", geom=_square(-4.0, 5.0),
        )
        fetched = District.objects.get(pk=district.pk)
        assert fetched.geom is not None
        assert fetched.geom.geom_type == "MultiPolygon"
        assert fetched.geom.srid == 4326

    def test_commune_can_be_point(self):
        """Commune.geom accepte un Point (centroïde initial)."""
        district = District.objects.create(code="DST-TST", name="Test")
        commune = Commune.objects.create(
            code="C-TST", name="Test-Commune", district=district,
            geom=Point(-4.0, 5.0, srid=4326),
        )
        fetched = Commune.objects.get(pk=commune.pk)
        assert fetched.geom.geom_type == "Point"

    def test_commune_can_be_polygon(self):
        """Commune.geom accepte aussi un MultiPolygon (frontière OSM, B.6)."""
        district = District.objects.create(code="DST-TST2", name="Test2")
        commune = Commune.objects.create(
            code="C-TST2", name="Test-Commune-Poly", district=district,
            geom=_square(-4.0, 5.0),
        )
        fetched = Commune.objects.get(pk=commune.pk)
        assert fetched.geom.geom_type == "MultiPolygon"


class TestProtection:
    """on_delete=PROTECT évite les suppressions cascading destructrices."""

    def test_cannot_delete_district_with_communes(self):
        from django.db.models.deletion import ProtectedError

        district = District.objects.create(code="ZAN2", name="Zanzan2")
        Commune.objects.create(code="C-X", name="X", district=district)

        with pytest.raises(ProtectedError):
            district.delete()
