"""
Retrait du champ `osm_id` (et de sa contrainte + index) sur RoadSegment,
FloodRisk et VegetationDensity.

Contexte : abandon d'OpenStreetMap comme source de données métier. La clé
d'unicité/mise à jour n'est plus l'identifiant OSM — les données proviendront
désormais d'imports (BDR, terrain) via l'importeur générique (Phase A).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0009_backfill_is_strategic'),
    ]

    operations = [
        # Contraintes d'unicité conditionnelles
        migrations.RemoveConstraint(
            model_name='roadsegment', name='unique_road_osm_id_per_zone',
        ),
        migrations.RemoveConstraint(
            model_name='floodrisk', name='unique_flood_osm_id_per_zone',
        ),
        migrations.RemoveConstraint(
            model_name='vegetationdensity', name='unique_veg_osm_id_per_zone',
        ),
        # Index composites (zone, osm_id)
        migrations.RemoveIndex(model_name='roadsegment', name='road_zone_osm_idx'),
        migrations.RemoveIndex(model_name='floodrisk', name='flood_zone_osm_idx'),
        migrations.RemoveIndex(model_name='vegetationdensity', name='veg_zone_osm_idx'),
        # Champs
        migrations.RemoveField(model_name='roadsegment', name='osm_id'),
        migrations.RemoveField(model_name='floodrisk', name='osm_id'),
        migrations.RemoveField(model_name='vegetationdensity', name='osm_id'),
    ]
