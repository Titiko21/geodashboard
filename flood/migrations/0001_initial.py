import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("admin_divisions", "0005_commune_district_nullable"),
    ]

    operations = [
        migrations.CreateModel(
            name="CommuneFloodSusceptibility",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("elevation_mean_m", models.FloatField(blank=True, null=True, verbose_name="Altitude moyenne (m)")),
                ("elevation_min_m", models.FloatField(blank=True, null=True, verbose_name="Altitude minimale (m)")),
                ("slope_mean_deg", models.FloatField(blank=True, null=True, verbose_name="Pente moyenne (°)")),
                ("hand_mean_m", models.FloatField(blank=True, null=True, help_text="Hauteur au-dessus du drainage le plus proche (MERIT Hydro)", verbose_name="HAND moyen (m)")),
                ("urban_pct", models.FloatField(blank=True, null=True, verbose_name="% bâti (Dynamic World)")),
                ("water_pct", models.FloatField(blank=True, null=True, verbose_name="% eau de surface (Dynamic World)")),
                ("score_hand", models.FloatField(blank=True, null=True)),
                ("score_elevation", models.FloatField(blank=True, null=True)),
                ("score_slope", models.FloatField(blank=True, null=True)),
                ("score_impervious", models.FloatField(blank=True, null=True)),
                ("score_water", models.FloatField(blank=True, null=True)),
                ("susceptibility", models.FloatField(verbose_name="Susceptibilité (0-100)")),
                ("level", models.CharField(choices=[("faible", "Faible"), ("modere", "Modéré"), ("eleve", "Élevé"), ("critique", "Critique")], max_length=10)),
                ("computed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("sources", models.CharField(default="Copernicus GLO-30 · MERIT Hydro (HAND) · Dynamic World", max_length=200)),
                ("commune", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="flood_susceptibility", to="admin_divisions.commune", verbose_name="Commune")),
            ],
            options={
                "verbose_name": "Susceptibilité inondation (commune)",
                "verbose_name_plural": "Susceptibilités inondation (communes)",
                "ordering": ["-susceptibility"],
            },
        ),
    ]
