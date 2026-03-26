from django.db import models
from django.utils import timezone


class Zone(models.Model):
    """Zone géographique surveillée"""
    name        = models.CharField(max_length=100, verbose_name="Nom")
    code        = models.CharField(max_length=20, unique=True, verbose_name="Code")
    lat_center  = models.FloatField(verbose_name="Latitude")
    lng_center  = models.FloatField(verbose_name="Longitude")
    description = models.TextField(blank=True)

    class Meta:
        verbose_name        = "Zone"
        verbose_name_plural = "Zones"
        ordering            = ['name']

    def __str__(self):
        return self.name


class RoadSegment(models.Model):
    """Segment routier avec son état"""
    STATUS_CHOICES = [
        ('bon',      'Bon état'),
        ('degrade',  'Dégradé'),
        ('critique', 'Critique'),
        ('ferme',    'Fermé'),
    ]
    SURFACE_CHOICES = [
        ('bitume',  'Bitume'),
        ('terre',   'Terre'),
        ('pave',    'Pavé'),
        ('gravier', 'Gravier'),
        ('autre',   'Autre'),
    ]

    zone            = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name='roads')
    osm_id          = models.BigIntegerField(
                          null=True, blank=True, db_index=True,
                          verbose_name="ID OpenStreetMap",
                          help_text="Identifiant unique OSM — clé de mise à jour lors des imports",
                      )
    name            = models.CharField(max_length=200, verbose_name="Nom")
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default='bon')
    condition_score = models.FloatField(help_text="Score de 0 (mauvais) à 100 (excellent)")
    surface_type    = models.CharField(
                          max_length=20, choices=SURFACE_CHOICES, default='bitume',
                          blank=True, verbose_name="Type de surface",
                      )
    geojson         = models.JSONField(default=dict, help_text="GeoJSON LineString du tracé")
    last_analyzed   = models.DateTimeField(default=timezone.now)
    notes           = models.TextField(blank=True)

    class Meta:
        verbose_name        = "Segment routier"
        verbose_name_plural = "Segments routiers"
        ordering            = ['condition_score']
        constraints         = [
            models.UniqueConstraint(
                fields=['zone', 'osm_id'],
                condition=models.Q(osm_id__isnull=False),
                name='unique_road_osm_id_per_zone',
            )
        ]
        indexes = [
            models.Index(fields=['zone', 'osm_id'], name='road_zone_osm_idx'),
        ]

    def __str__(self):
        return f"{self.name} [{self.get_status_display()}]"


class FloodRisk(models.Model):
    """Zone à risque d'inondation"""
    RISK_CHOICES = [
        ('faible',   'Faible'),
        ('modere',   'Modéré'),
        ('eleve',    'Élevé'),
        ('critique', 'Critique'),
    ]

    zone          = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name='flood_risks')
    osm_id        = models.BigIntegerField(
                        null=True, blank=True, db_index=True,
                        verbose_name="ID OpenStreetMap",
                        help_text="Identifiant unique OSM — clé de mise à jour lors des imports",
                    )
    name          = models.CharField(max_length=200, verbose_name="Nom")
    risk_level    = models.CharField(max_length=20, choices=RISK_CHOICES, default='faible')
    risk_score    = models.FloatField(help_text="Score de risque 0-100")
    area_km2      = models.FloatField(help_text="Surface en km²")
    rainfall_mm   = models.FloatField(default=0, help_text="Précipitations récentes en mm")
    geojson       = models.JSONField(default=dict, help_text="GeoJSON Polygon de la zone")
    last_analyzed = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name        = "Zone d'inondation"
        verbose_name_plural = "Zones d'inondation"
        ordering            = ['-risk_score']
        constraints         = [
            models.UniqueConstraint(
                fields=['zone', 'osm_id'],
                condition=models.Q(osm_id__isnull=False),
                name='unique_flood_osm_id_per_zone',
            )
        ]
        indexes = [
            models.Index(fields=['zone', 'osm_id'], name='flood_zone_osm_idx'),
        ]

    def __str__(self):
        return f"{self.name} — {self.get_risk_level_display()}"


class VegetationDensity(models.Model):
    """Densité de végétation (NDVI)"""
    DENSITY_CHOICES = [
        ('sparse',    'Éparse'),
        ('moderate',  'Modérée'),
        ('dense',     'Dense'),
        ('very_dense','Très dense'),
    ]

    zone               = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name='vegetation')
    osm_id             = models.BigIntegerField(
                             null=True, blank=True, db_index=True,
                             verbose_name="ID OpenStreetMap",
                             help_text="Identifiant unique OSM — clé de mise à jour lors des imports",
                         )
    name               = models.CharField(max_length=200, verbose_name="Nom")
    ndvi_value         = models.FloatField(help_text="NDVI de -1 à 1")
    density_class      = models.CharField(max_length=20, choices=DENSITY_CHOICES)
    coverage_percent   = models.FloatField(help_text="% de couverture végétale")
    change_vs_previous = models.FloatField(
                             default=0,
                             help_text="Variation NDVI vs analyse précédente",
                         )
    geojson            = models.JSONField(default=dict, help_text="GeoJSON Polygon de la zone")
    last_analyzed      = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name        = "Végétation"
        verbose_name_plural = "Végétations"
        constraints         = [
            models.UniqueConstraint(
                fields=['zone', 'osm_id'],
                condition=models.Q(osm_id__isnull=False),
                name='unique_veg_osm_id_per_zone',
            )
        ]
        indexes = [
            models.Index(fields=['zone', 'osm_id'], name='veg_zone_osm_idx'),
        ]

    def __str__(self):
        return f"{self.name} — NDVI {self.ndvi_value:.2f}"


class Alert(models.Model):
    """Alerte générée par les analyses"""
    SEVERITY_CHOICES = [
        ('info',     'Information'),
        ('warning',  'Avertissement'),
        ('danger',   'Danger'),
        ('critical', 'Critique'),
    ]
    CATEGORY_CHOICES = [
        ('road',       'Route'),
        ('flood',      'Inondation'),
        ('vegetation', 'Végétation'),
        ('system',     'Système'),
    ]

    zone       = models.ForeignKey(
                     Zone, on_delete=models.CASCADE, related_name='alerts',
                     null=True, blank=True,
                 )
    title      = models.CharField(max_length=200)
    message    = models.TextField()
    severity   = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    category   = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    lat        = models.FloatField(null=True, blank=True)
    lng        = models.FloatField(null=True, blank=True)

    class Meta:
        verbose_name        = "Alerte"
        verbose_name_plural = "Alertes"
        ordering            = ['-created_at']

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"