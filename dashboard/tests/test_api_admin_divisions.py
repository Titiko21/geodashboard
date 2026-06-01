"""
Test du contrat de l'API /api/admin/divisions/ (Phase B mini-visu).

Couvre :
  - structure FeatureCollection
  - 3 niveaux de query : district (défaut), region, all
  - propriétés requises (level, code, name, is_autonomous pour district,
    district pour region)
  - skip silencieux des entités sans geom
"""
import json

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon

from admin_divisions.models import District, Region


pytestmark = pytest.mark.django_db


def _square(lng, lat, size=0.1):
    p = Polygon((
        (lng, lat), (lng + size, lat),
        (lng + size, lat + size), (lng, lat + size),
        (lng, lat),
    ))
    return MultiPolygon(p, srid=4326)


@pytest.fixture
def sample_admin_data():
    """Un district autonome + un district normal + une région rattachée."""
    abj = District.objects.create(
        code="CI01", name="Abidjan", is_autonomous=True, geom=_square(-4.0, 5.3),
    )
    lagunes = District.objects.create(
        code="CIV-DIS-LAGUNES", name="Lagunes", geom=_square(-4.5, 5.5),
    )
    region = Region.objects.create(
        code="CI03", name="Agneby-Tiassa", district=lagunes, geom=_square(-4.4, 5.6),
    )
    return abj, lagunes, region


class TestStructure:
    def test_default_level_is_district(self, auth_client, sample_admin_data):
        resp = auth_client.get("/api/admin/divisions/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["type"] == "FeatureCollection"
        assert isinstance(data["features"], list)

    def test_invalid_level_returns_400(self, auth_client):
        resp = auth_client.get("/api/admin/divisions/?level=garbage")
        assert resp.status_code == 400


class TestDistrictLevel:
    def test_returns_only_districts(self, auth_client, sample_admin_data):
        resp = auth_client.get("/api/admin/divisions/?level=district")
        data = json.loads(resp.content)
        assert len(data["features"]) == 2
        for feat in data["features"]:
            assert feat["properties"]["level"] == "district"
            assert "is_autonomous" in feat["properties"]

    def test_autonomous_flag_propagated(self, auth_client, sample_admin_data):
        resp = auth_client.get("/api/admin/divisions/?level=district")
        data = json.loads(resp.content)
        names = {f["properties"]["name"]: f["properties"]["is_autonomous"]
                 for f in data["features"]}
        assert names["Abidjan"] is True
        assert names["Lagunes"] is False


class TestRegionLevel:
    def test_returns_only_regions(self, auth_client, sample_admin_data):
        resp = auth_client.get("/api/admin/divisions/?level=region")
        data = json.loads(resp.content)
        assert len(data["features"]) == 1
        feat = data["features"][0]
        assert feat["properties"]["level"] == "region"
        assert feat["properties"]["name"] == "Agneby-Tiassa"
        assert feat["properties"]["district"] == "Lagunes"


class TestAllLevel:
    def test_returns_districts_and_regions(self, auth_client, sample_admin_data):
        resp = auth_client.get("/api/admin/divisions/?level=all")
        data = json.loads(resp.content)
        # 2 districts + 1 région = 3
        assert len(data["features"]) == 3
        levels = [f["properties"]["level"] for f in data["features"]]
        assert levels.count("district") == 2
        assert levels.count("region") == 1


class TestSkipMissingGeom:
    def test_district_without_geom_is_skipped(self, auth_client):
        District.objects.create(code="X-NO-GEOM", name="NoGeom", geom=None)
        resp = auth_client.get("/api/admin/divisions/?level=district")
        data = json.loads(resp.content)
        assert len(data["features"]) == 0
