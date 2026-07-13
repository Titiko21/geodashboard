"""
Susceptibilité aux inondations par commune.

Les FACTEURS BRUTS sont stockés séparément des sous-scores et de l'indice :
on peut re-pondérer (scoring.WEIGHTS) et recalculer sans re-consommer GEE.
"""
from django.contrib.gis.db import models
from django.utils import timezone


class FloodEvent(models.Model):
    """
    Événement d'inondation OBSERVÉ (vérité terrain) — importable via
    l'importeur générique (cible « flood_event », GeoJSON point/polygone).

    Compte dans le facteur « historique » du scoring : les communes dont
    le territoire intersecte des événements récents voient leur
    susceptibilité remonter mécaniquement.
    """
    code   = models.CharField(max_length=64, unique=True,
                              help_text="Clé stable d'upsert (générée à l'import)")
    name   = models.CharField(max_length=200, verbose_name="Lieu / description")
    date   = models.DateField(null=True, blank=True, verbose_name="Date")
    source = models.CharField(max_length=200, blank=True, default="",
                              verbose_name="Source (presse, ONPC, relevé…)")
    geom   = models.GeometryField(srid=4326, spatial_index=True,
                                  help_text="Point ou polygone de l'événement")

    class Meta:
        verbose_name        = "Événement d'inondation"
        verbose_name_plural = "Événements d'inondation"
        ordering            = ["-date", "name"]

    def __str__(self):
        return f"{self.name}" + (f" ({self.date})" if self.date else "")


class CommuneFloodSusceptibility(models.Model):
    LEVEL_CHOICES = [
        ("faible",   "Faible"),
        ("modere",   "Modéré"),
        ("eleve",    "Élevé"),
        ("critique", "Critique"),
    ]

    commune = models.OneToOneField(
        "admin_divisions.Commune", on_delete=models.CASCADE,
        related_name="flood_susceptibility", verbose_name="Commune",
    )

    # ── Facteurs bruts (None = indisponible à la date du calcul) ──
    elevation_mean_m = models.FloatField(null=True, blank=True, verbose_name="Altitude moyenne (m)")
    elevation_min_m  = models.FloatField(null=True, blank=True, verbose_name="Altitude minimale (m)")
    slope_mean_deg   = models.FloatField(null=True, blank=True, verbose_name="Pente moyenne (°)")
    hand_mean_m      = models.FloatField(null=True, blank=True, verbose_name="HAND moyen (m)",
                                         help_text="Hauteur au-dessus du drainage le plus proche (MERIT Hydro)")
    urban_pct        = models.FloatField(null=True, blank=True, verbose_name="% bâti (Dynamic World)")
    water_pct        = models.FloatField(null=True, blank=True, verbose_name="% eau de surface (Dynamic World)")
    hand_low_pct     = models.FloatField(null=True, blank=True, verbose_name="% en zone basse (HAND < 5 m)")
    flat_pct         = models.FloatField(null=True, blank=True, verbose_name="% quasi plat (pente < 2°)")
    built_low_pct    = models.FloatField(null=True, blank=True, verbose_name="% du bâti en zone basse",
                                         help_text="Exposition : quartiers construits en bas-fond")
    history_events   = models.IntegerField(null=True, blank=True,
                                           verbose_name="Événements d'inondation observés")

    # ── Sous-scores 0-100 (dérivés des facteurs, cf. flood/scoring.py) ──
    score_hand_low   = models.FloatField(null=True, blank=True)
    score_exposure   = models.FloatField(null=True, blank=True)
    score_history    = models.FloatField(null=True, blank=True)
    score_elevation  = models.FloatField(null=True, blank=True)
    score_impervious = models.FloatField(null=True, blank=True)
    score_water      = models.FloatField(null=True, blank=True)
    score_flat       = models.FloatField(null=True, blank=True)

    # ── Indice agrégé ──
    physio_susceptibility = models.FloatField(
        null=True, blank=True, verbose_name="Susceptibilité physiographique (satellite)",
        help_text="Score calculé sur le terrain seul, avant plancher observé.",
    )
    susceptibility = models.FloatField(
        verbose_name="Susceptibilité retenue (0-100)",
        help_text="max(physiographique, plancher imposé par les inondations observées).",
    )
    level          = models.CharField(max_length=10, choices=LEVEL_CHOICES)

    computed_at = models.DateTimeField(default=timezone.now)
    sources     = models.CharField(
        max_length=200,
        default="Copernicus GLO-30 · MERIT Hydro (HAND) · Dynamic World",
    )

    class Meta:
        verbose_name        = "Susceptibilité inondation (commune)"
        verbose_name_plural = "Susceptibilités inondation (communes)"
        ordering            = ["-susceptibility"]

    def __str__(self):
        return f"{self.commune.name} — {self.susceptibility:.0f}/100 ({self.get_level_display()})"
