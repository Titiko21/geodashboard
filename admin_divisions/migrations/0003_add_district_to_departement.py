"""
Migration 0003 — Ajoute `Departement.district` et rend `Departement.region`
nullable.

Motivation : HDX cod-ab-civ ADM2 contient 108 départements, dont 3 sont logés
dans les districts autonomes (Abidjan, Yamoussoukro) et n'ont pas de Région
parente. Le modèle doit donc autoriser `region IS NULL` et exiger
`district NOT NULL` (mirroir du pattern déjà appliqué à `Commune`).

Stratégie sûre :
  1. Ajout `district` en nullable.
  2. Backfill : pour chaque département existant, `district = region.district`.
     (No-op aujourd'hui car la table est vide, mais le code reste safe si la
     migration est jouée après un import partiel.)
  3. Passe `district` en non-nullable.
  4. Passe `region` en nullable.
"""
import django.db.models.deletion
from django.db import migrations, models


def backfill_district(apps, schema_editor):
    """Pour chaque département déjà importé, hérite du district via la région."""
    Departement = apps.get_model("admin_divisions", "Departement")
    for dep in Departement.objects.select_related("region").all():
        if dep.region_id and dep.district_id is None:
            dep.district_id = dep.region.district_id
            dep.save(update_fields=["district"])


def backfill_district_reverse(apps, schema_editor):
    """Migration descendante : noop (les colonnes seront recréées par les AlterField)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("admin_divisions", "0002_extend_code_length"),
    ]

    operations = [
        # 1. Ajout en nullable pour permettre le backfill
        migrations.AddField(
            model_name="departement",
            name="district",
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="departements",
                to="admin_divisions.district",
                verbose_name="District",
            ),
        ),

        # 2. Backfill safe (no-op si table vide)
        migrations.RunPython(backfill_district, backfill_district_reverse),

        # 3. Passe district en non-nullable
        migrations.AlterField(
            model_name="departement",
            name="district",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="departements",
                to="admin_divisions.district",
                verbose_name="District",
            ),
        ),

        # 4. Passe region en nullable
        migrations.AlterField(
            model_name="departement",
            name="region",
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="departements",
                to="admin_divisions.region",
                verbose_name="Région",
            ),
        ),
    ]
