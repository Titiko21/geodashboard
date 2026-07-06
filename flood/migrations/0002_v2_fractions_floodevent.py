"""
Scoring v2 : fractions de territoire (bas-fonds, plat), exposition du bâti,
historique des inondations observées (nouveau modèle FloodEvent).
Retrait des sous-scores v1 basés sur des moyennes (hand/slope).
"""
import django.contrib.gis.db.models.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flood", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FloodEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(help_text="Clé stable d'upsert (générée à l'import)", max_length=64, unique=True)),
                ("name", models.CharField(max_length=200, verbose_name="Lieu / description")),
                ("date", models.DateField(blank=True, null=True, verbose_name="Date")),
                ("source", models.CharField(blank=True, default="", max_length=200, verbose_name="Source (presse, ONPC, relevé…)")),
                ("geom", django.contrib.gis.db.models.fields.GeometryField(help_text="Point ou polygone de l'événement", srid=4326)),
            ],
            options={
                "verbose_name": "Événement d'inondation",
                "verbose_name_plural": "Événements d'inondation",
                "ordering": ["-date", "name"],
            },
        ),
        migrations.RemoveField(model_name="communefloodsusceptibility", name="score_hand"),
        migrations.RemoveField(model_name="communefloodsusceptibility", name="score_slope"),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="hand_low_pct",
            field=models.FloatField(blank=True, null=True, verbose_name="% en zone basse (HAND < 5 m)"),
        ),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="flat_pct",
            field=models.FloatField(blank=True, null=True, verbose_name="% quasi plat (pente < 2°)"),
        ),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="built_low_pct",
            field=models.FloatField(blank=True, null=True, help_text="Exposition : quartiers construits en bas-fond", verbose_name="% du bâti en zone basse"),
        ),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="history_events",
            field=models.IntegerField(blank=True, null=True, verbose_name="Événements d'inondation observés"),
        ),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="score_hand_low",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="score_exposure",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="score_history",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="communefloodsusceptibility", name="score_flat",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
