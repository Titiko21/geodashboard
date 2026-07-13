"""
v3 : sépare la susceptibilité physiographique (satellite) du score retenu
(max avec le plancher imposé par les inondations observées).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flood", "0002_v2_fractions_floodevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="communefloodsusceptibility",
            name="physio_susceptibility",
            field=models.FloatField(
                blank=True, null=True,
                help_text="Score calculé sur le terrain seul, avant plancher observé.",
                verbose_name="Susceptibilité physiographique (satellite)",
            ),
        ),
        migrations.AlterField(
            model_name="communefloodsusceptibility",
            name="susceptibility",
            field=models.FloatField(
                help_text="max(physiographique, plancher imposé par les inondations observées).",
                verbose_name="Susceptibilité retenue (0-100)",
            ),
        ),
    ]
