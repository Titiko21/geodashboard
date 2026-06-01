"""
Migration initiale de l'app admin_divisions.

Crée les 5 tables (District, Région, Département, Sous-préfecture, Commune)
avec leurs FK et GeometryField PostGIS (avec spatial_index GIST). Aucune
donnée — l'import est fait par des commandes dédiées en B.2 → B.5.
"""
import django.contrib.gis.db.models.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="District",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=20, unique=True, verbose_name="Code")),
                ("name", models.CharField(max_length=100, verbose_name="Nom")),
                ("is_autonomous", models.BooleanField(
                    default=False,
                    help_text="True pour Abidjan et Yamoussoukro — sautent les niveaux Région/Département/Sous-préf.",
                )),
                ("geom", django.contrib.gis.db.models.fields.MultiPolygonField(
                    srid=4326, null=True, blank=True,
                    help_text="Frontière du district (HDX).",
                )),
            ],
            options={
                "verbose_name": "District",
                "verbose_name_plural": "Districts",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Region",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=20, unique=True, verbose_name="Code")),
                ("name", models.CharField(max_length=100, verbose_name="Nom")),
                ("geom", django.contrib.gis.db.models.fields.MultiPolygonField(
                    srid=4326, null=True, blank=True,
                    help_text="Frontière de la région (HDX).",
                )),
                ("district", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="regions", to="admin_divisions.district",
                    verbose_name="District",
                )),
            ],
            options={
                "verbose_name": "Région",
                "verbose_name_plural": "Régions",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Departement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=20, unique=True, verbose_name="Code")),
                ("name", models.CharField(max_length=100, verbose_name="Nom")),
                ("geom", django.contrib.gis.db.models.fields.MultiPolygonField(
                    srid=4326, null=True, blank=True,
                    help_text="Frontière du département (GADM).",
                )),
                ("region", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="departements", to="admin_divisions.region",
                    verbose_name="Région",
                )),
            ],
            options={
                "verbose_name": "Département",
                "verbose_name_plural": "Départements",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="SousPrefecture",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=20, unique=True, verbose_name="Code")),
                ("name", models.CharField(max_length=100, verbose_name="Nom")),
                ("geom", django.contrib.gis.db.models.fields.MultiPolygonField(
                    srid=4326, null=True, blank=True,
                    help_text="Frontière de la sous-préfecture (HDX ADM3).",
                )),
                ("departement", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="sous_prefectures", to="admin_divisions.departement",
                    verbose_name="Département",
                )),
            ],
            options={
                "verbose_name": "Sous-préfecture",
                "verbose_name_plural": "Sous-préfectures",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Commune",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=20, unique=True, verbose_name="Code")),
                ("name", models.CharField(max_length=100, verbose_name="Nom")),
                ("geom", django.contrib.gis.db.models.fields.GeometryField(
                    srid=4326, null=True, blank=True,
                    help_text="Point (centroïde) au démarrage, polygone après import OSM (B.6).",
                )),
                ("district", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="communes", to="admin_divisions.district",
                    verbose_name="District",
                )),
                ("region", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="communes", to="admin_divisions.region",
                    null=True, blank=True, verbose_name="Région",
                )),
                ("departement", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="communes", to="admin_divisions.departement",
                    null=True, blank=True, verbose_name="Département",
                )),
                ("sous_prefecture", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="communes", to="admin_divisions.sousprefecture",
                    null=True, blank=True, verbose_name="Sous-préfecture",
                )),
            ],
            options={
                "verbose_name": "Commune",
                "verbose_name_plural": "Communes",
                "ordering": ["name"],
            },
        ),
    ]
