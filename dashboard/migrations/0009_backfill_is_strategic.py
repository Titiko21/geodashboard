"""
Backfill ponctuel de `RoadSegment.is_strategic` à partir du type OSM stocké
dans `notes` ("Type OSM : <highway> | …").

Remplace l'ancien filtre `notes__iregex` (scan séquentiel sur ~128k lignes,
~4 s par requête) par un booléen indexé. Cette migration ne marque qu'une
fois les routes existantes ; les imports suivants peuplent le champ
directement (cf. populate_geodata._build_road_defaults).
"""
from django.db import migrations

STRATEGIC_REGEX = r"Type OSM : (motorway|trunk|primary|secondary)"


def set_strategic(apps, schema_editor):
    RoadSegment = apps.get_model("dashboard", "RoadSegment")
    RoadSegment.objects.filter(notes__iregex=STRATEGIC_REGEX).update(is_strategic=True)


def unset_strategic(apps, schema_editor):
    RoadSegment = apps.get_model("dashboard", "RoadSegment")
    RoadSegment.objects.update(is_strategic=False)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0008_roadsegment_is_strategic"),
    ]

    operations = [
        migrations.RunPython(set_strategic, unset_strategic),
    ]
