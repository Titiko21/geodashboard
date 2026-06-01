"""
Migration 0005 — Ajout des GeometryField PostGIS.

Ajoute un champ `geom` (LineString/Polygon WGS84, nullable) à RoadSegment,
FloodRisk et VegetationDensity, en parallèle des `geojson` existants. La
population des valeurs sera effectuée par la migration 0006 (data migration).

Les `spatial_index=True` (défaut Django) génèrent automatiquement les indexes
GIST nécessaires aux requêtes ST_Within, ST_Intersects, ST_DWithin, etc.
"""
import django.contrib.gis.db.models.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0004_roadsection'),
    ]

    operations = [
        migrations.AddField(
            model_name='roadsegment',
            name='geom',
            field=django.contrib.gis.db.models.fields.GeometryField(
                blank=True,
                help_text="Géométrie PostGIS (LineString WGS84) — peuplée depuis `geojson`",
                null=True,
                srid=4326,
            ),
        ),
        migrations.AddField(
            model_name='floodrisk',
            name='geom',
            field=django.contrib.gis.db.models.fields.GeometryField(
                blank=True,
                help_text="Géométrie PostGIS (Polygon WGS84) — peuplée depuis `geojson`",
                null=True,
                srid=4326,
            ),
        ),
        migrations.AddField(
            model_name='vegetationdensity',
            name='geom',
            field=django.contrib.gis.db.models.fields.GeometryField(
                blank=True,
                help_text="Géométrie PostGIS (Polygon WGS84) — peuplée depuis `geojson`",
                null=True,
                srid=4326,
            ),
        ),
    ]
