from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Lien direct Alert -> objet source (route / inondation / végétation).

    Ajoute source_type et source_id afin de repositionner chaque alerte sur la
    géométrie exacte de son objet d'origine, sans passer par une résolution par
    nom (qui confond les objets homonymes).
    """

    dependencies = [
        ("dashboard", "0006_populate_geom_from_geojson"),
    ]

    operations = [
        migrations.AddField(
            model_name="alert",
            name="source_type",
            field=models.CharField(
                max_length=20,
                null=True,
                blank=True,
                choices=[
                    ("road", "Route"),
                    ("flood", "Inondation"),
                    ("vegetation", "Végétation"),
                    ("system", "Système"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="alert",
            name="source_id",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
    ]
