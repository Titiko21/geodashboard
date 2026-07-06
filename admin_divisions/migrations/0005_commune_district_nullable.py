"""
Rend la FK Commune → District nullable.

Contexte : la base ne conserve que les communes (source GeoJSON Grand
Abidjan). Les villes et districts créés par l'ancien import sont purgés ;
la hiérarchie sera raccrochée plus tard lors des imports BDR.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("admin_divisions", "0004_alter_commune_id_alter_departement_geom_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="commune",
            name="district",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="communes",
                to="admin_divisions.district",
                verbose_name="District",
            ),
        ),
    ]
