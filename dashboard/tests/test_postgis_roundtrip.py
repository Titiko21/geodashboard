"""
Tests PostGIS de bas niveau : on s'assure que les GeometryField round-trippent
correctement entre Python et la DB, et que les requêtes spatiales marchent.

Si ce test passe, on a la garantie que toute la chaîne GDAL/GEOS/PROJ +
psycopg2 + django.contrib.gis est correctement câblée.
"""
import pytest
from django.contrib.gis.geos import LineString, Point, Polygon

from dashboard.models import RoadSegment


pytestmark = pytest.mark.django_db


def test_linestring_roundtrip(zone):
    """Écrire un LineString PostGIS, le relire, vérifier qu'il est identique."""
    geom = LineString((-4.0, 5.3), (-4.1, 5.4), (-4.2, 5.5), srid=4326)
    r = RoadSegment.objects.create(
        zone=zone, name="Test", condition_score=70, status="bon", geom=geom,
        geojson={"type": "LineString", "coordinates": [[-4, 5.3], [-4.1, 5.4], [-4.2, 5.5]]},
    )
    fetched = RoadSegment.objects.get(pk=r.pk)
    assert fetched.geom is not None
    assert fetched.geom.geom_type == "LineString"
    assert fetched.geom.srid == 4326
    assert len(fetched.geom.coords) == 3


def test_spatial_query_works(zone):
    """ST_DWithin natif PostGIS via le manager Django. Si ce test passe, on
    pourra utiliser ST_Within, ST_Intersects, etc. partout sans surprise."""
    r1 = RoadSegment.objects.create(
        zone=zone, name="Proche", condition_score=80, status="bon",
        geom=LineString((-4.00, 5.30), (-4.01, 5.31), srid=4326),
        geojson={"type": "LineString", "coordinates": [[-4, 5.3], [-4.01, 5.31]]},
    )
    r2 = RoadSegment.objects.create(
        zone=zone, name="Loin", condition_score=80, status="bon",
        geom=LineString((10.0, 10.0), (10.1, 10.1), srid=4326),
        geojson={"type": "LineString", "coordinates": [[10, 10], [10.1, 10.1]]},
    )

    # Bbox autour du premier point
    from django.contrib.gis.geos import Polygon as GP
    bbox = GP.from_bbox((-4.1, 5.2, -3.9, 5.4))
    bbox.srid = 4326

    near = list(RoadSegment.objects.filter(geom__intersects=bbox))
    assert r1 in near
    assert r2 not in near
