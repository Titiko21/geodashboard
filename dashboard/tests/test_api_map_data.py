"""
Tests du contrat de l'API /api/map-data/.

Cette API est consommée par dashboard.js → _loadMapData(). Le front s'attend
à une structure précise (routes/floods/vegetation avec leurs champs). Si on
change la signature en backend, ce test pète et on évite une régression UI
silencieuse.

NOTE : on utilise `auth_client` (fixture conftest) et non `client`, car la vue
est derrière LoginRequiredMiddleware. Un test non authentifié reçoit 302.
"""
import json

import pytest

from dashboard.models import FloodRisk, RoadSegment, VegetationDensity


pytestmark = pytest.mark.django_db


def test_returns_three_keys(auth_client):
    resp = auth_client.get("/api/map-data/")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert set(data.keys()) == {"routes", "floods", "vegetation"}


def test_road_segment_has_required_fields(auth_client, zone):
    RoadSegment.objects.create(
        zone=zone,
        name="Avenue Test",
        condition_score=75,
        status="bon",
        surface_type="bitume",
        geojson={"type": "LineString", "coordinates": [[-4.0, 5.3], [-4.1, 5.4]]},
    )

    # focus=all : réseau complet (par défaut, seuls les axes structurants
    # `is_strategic=True` sortent — cf. _apply_strategic_filter).
    resp = auth_client.get("/api/map-data/?focus=all")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert len(data["routes"]) == 1

    route = data["routes"][0]
    expected = {
        "id", "name", "condition_score", "status", "status_label",
        "surface_type", "color", "notes", "geojson",
    }
    assert expected.issubset(route.keys()), \
        f"Champs manquants : {expected - set(route.keys())}"


def test_flood_risk_has_required_fields(auth_client, zone):
    FloodRisk.objects.create(
        zone=zone,
        name="Marais Test",
        risk_level="modere",
        risk_score=45,
        area_km2=1.2,
        rainfall_mm=0,
        geojson={"type": "Polygon", "coordinates": [[[-4, 5], [-4.1, 5], [-4.1, 5.1], [-4, 5]]]},
    )

    resp = auth_client.get("/api/map-data/")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    flood = data["floods"][0]
    expected = {"id", "name", "risk_level", "risk_label", "risk_score",
                "area_km2", "rainfall_mm", "color", "geojson"}
    assert expected.issubset(flood.keys())


def test_vegetation_has_required_fields(auth_client, zone):
    VegetationDensity.objects.create(
        zone=zone,
        name="Forêt Test",
        ndvi_value=0.65,
        density_class="dense",
        coverage_percent=72,
        geojson={"type": "Polygon", "coordinates": [[[-4, 5], [-4.1, 5], [-4.1, 5.1], [-4, 5]]]},
    )

    resp = auth_client.get("/api/map-data/")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    veg = data["vegetation"][0]
    expected = {"id", "name", "ndvi_value", "coverage_percent",
                "density_class", "density_label", "geojson"}
    assert expected.issubset(veg.keys())


def test_geom_postgis_takes_priority_over_geojson_field(auth_client, zone):
    """Si la ligne a un `geom` PostGIS, c'est lui qui sort en sortie API,
    pas le JSONField legacy. Sécurise le Lot 4."""
    from django.contrib.gis.geos import LineString
    r = RoadSegment.objects.create(
        zone=zone,
        name="Avenue Conflit",
        condition_score=70,
        status="bon",
        geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},  # legacy
        geom=LineString((-4.0, 5.3), (-4.1, 5.4), srid=4326),               # vérité
    )

    resp = auth_client.get("/api/map-data/?focus=all")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    geo = data["routes"][0]["geojson"]
    # Le `geom` PostGIS gagne — coords [-4.x, 5.x], pas [0,0]/[1,1]
    assert geo["coordinates"][0] == [-4.0, 5.3]
    assert geo["coordinates"][1] == [-4.1, 5.4]


def test_zone_filter(auth_client, zone):
    """?zone=<code> ne renvoie que les objets de la zone demandée."""
    other = zone.__class__.objects.create(
        name="Autre", code="OTH", lat_center=6, lng_center=-5,
    )
    RoadSegment.objects.create(zone=zone, name="A", condition_score=50, status="degrade",
                               geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]})
    RoadSegment.objects.create(zone=other, name="B", condition_score=80, status="bon",
                               geojson={"type": "LineString", "coordinates": [[2, 2], [3, 3]]})

    resp = auth_client.get("/api/map-data/?zone=TST&focus=all")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert len(data["routes"]) == 1
    assert data["routes"][0]["name"] == "A"


def test_default_focus_hides_non_strategic_roads(auth_client, zone):
    """Sans ?focus=all, seuls les axes structurants (is_strategic) sortent —
    allègement du rendu carte par défaut."""
    RoadSegment.objects.create(zone=zone, name="Ruelle", condition_score=50,
                               status="degrade", is_strategic=False,
                               geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]})
    RoadSegment.objects.create(zone=zone, name="Autoroute", condition_score=90,
                               status="bon", is_strategic=True,
                               geojson={"type": "LineString", "coordinates": [[2, 2], [3, 3]]})

    resp = auth_client.get("/api/map-data/")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert [r["name"] for r in data["routes"]] == ["Autoroute"]
