"""
Susceptibilité aux inondations par commune.

Les FACTEURS BRUTS sont stockés séparément des sous-scores et de l'indice :
on peut re-pondérer (scoring.WEIGHTS) et recalculer sans re-consommer GEE.
"""
from django.db import models
from django.utils import timezone


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

    # ── Sous-scores 0-100 (dérivés des facteurs, cf. flood/scoring.py) ──
    score_hand       = models.FloatField(null=True, blank=True)
    score_elevation  = models.FloatField(null=True, blank=True)
    score_slope      = models.FloatField(null=True, blank=True)
    score_impervious = models.FloatField(null=True, blank=True)
    score_water      = models.FloatField(null=True, blank=True)

    # ── Indice agrégé ──
    susceptibility = models.FloatField(verbose_name="Susceptibilité (0-100)")
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
