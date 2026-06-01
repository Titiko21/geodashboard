"""
Migration 0002 — Élargit `code` de 20 → 30 chars sur les 5 modèles.

Motivation : les codes générés pour les districts non-autonomes (préfixe
CIV-DIS- + slug du nom) dépassent 20 caractères pour certains cas comme
"CIV-DIS-SASSANDRA-MARAHOUE" (26 chars). 30 chars donne une marge confortable
pour tous les cas connus.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("admin_divisions", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="district",
            name="code",
            field=models.CharField(max_length=30, unique=True, verbose_name="Code"),
        ),
        migrations.AlterField(
            model_name="region",
            name="code",
            field=models.CharField(max_length=30, unique=True, verbose_name="Code"),
        ),
        migrations.AlterField(
            model_name="departement",
            name="code",
            field=models.CharField(max_length=30, unique=True, verbose_name="Code"),
        ),
        migrations.AlterField(
            model_name="sousprefecture",
            name="code",
            field=models.CharField(max_length=30, unique=True, verbose_name="Code"),
        ),
        migrations.AlterField(
            model_name="commune",
            name="code",
            field=models.CharField(max_length=30, unique=True, verbose_name="Code"),
        ),
    ]
