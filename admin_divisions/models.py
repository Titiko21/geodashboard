"""
Découpage administratif de la Côte d'Ivoire.

Hiérarchie déconcentrée (services de l'État) :
    District (14)  →  Région (31)  →  Département (108)  →  Sous-préfecture (510)

Niveau décentralisé (collectivités locales) :
    Commune (197)   — parallèle aux sous-préfectures, FK directe vers District.

Cas particuliers (constatés sur HDX cod-ab-civ ADM2/ADM3) :
    - Districts autonomes (Abidjan, Yamoussoukro) : la hiérarchie saute le
      niveau Région mais conserve Département + Sous-préfecture.
        * Abidjan : 1 département (Abidjan) → 5 sous-préfectures.
        * Yamoussoukro : 2 départements (Yamoussoukro, Attiégouakro) → 4 SP.
      Pour ces 3 départements autonomes, `Departement.region` est NULL et
      `Departement.district` pointe directement sur le district autonome.
    - Une Commune rurale peut couvrir plusieurs sous-préfectures ; dans ce cas
      la FK `sous_prefecture` reste NULL (rattachement calculé par ST_Within
      à la demande).

Sources géométriques :
    - District / Région / Département / Sous-préfecture : HDX (cod-ab-civ).
      ADM1 = 33 entrées (31 régions + 2 districts autonomes), ADM2 = 108
      départements, ADM3 = 510 sous-préfectures.
    - Commune : centroïde initial (depuis dashboard.Zone) puis enrichissement
      OSM Overpass `admin_level=8` en B.6.
"""
from django.contrib.gis.db import models


class District(models.Model):
    """ADM1 — District (14, dont 2 autonomes : Abidjan, Yamoussoukro)."""

    code           = models.CharField(max_length=30, unique=True, verbose_name="Code")
    name           = models.CharField(max_length=100, verbose_name="Nom")
    is_autonomous  = models.BooleanField(
                         default=False,
                         help_text="True pour Abidjan et Yamoussoukro — sautent les niveaux Région/Département/Sous-préf.",
                     )
    geom           = models.MultiPolygonField(
                         srid=4326, null=True, blank=True, spatial_index=True,
                         help_text="Frontière du district (HDX).",
                     )

    class Meta:
        verbose_name        = "District"
        verbose_name_plural = "Districts"
        ordering            = ["name"]

    def __str__(self):
        suffix = " (autonome)" if self.is_autonomous else ""
        return f"{self.name}{suffix}"


class Region(models.Model):
    """ADM2 — Région (31). N'existe pas pour les districts autonomes."""

    code      = models.CharField(max_length=30, unique=True, verbose_name="Code")
    name      = models.CharField(max_length=100, verbose_name="Nom")
    district  = models.ForeignKey(
                    District, on_delete=models.PROTECT, related_name="regions",
                    verbose_name="District",
                )
    geom      = models.MultiPolygonField(
                    srid=4326, null=True, blank=True, spatial_index=True,
                    help_text="Frontière de la région (HDX).",
                )

    class Meta:
        verbose_name        = "Région"
        verbose_name_plural = "Régions"
        ordering            = ["name"]

    def __str__(self):
        return self.name


class Departement(models.Model):
    """
    Département (108) — niveau intermédiaire entre Région et Sous-préfecture.

    Rattaché obligatoirement à un District. La FK Région est nullable pour
    accueillir les 3 départements logés dans les districts autonomes
    (Abidjan, Yamoussoukro), où le niveau Région n'existe pas.
    """

    code      = models.CharField(max_length=30, unique=True, verbose_name="Code")
    name      = models.CharField(max_length=100, verbose_name="Nom")
    district  = models.ForeignKey(
                    District, on_delete=models.PROTECT, related_name="departements",
                    verbose_name="District",
                )
    region    = models.ForeignKey(
                    Region, on_delete=models.PROTECT, related_name="departements",
                    null=True, blank=True, verbose_name="Région",
                )
    geom      = models.MultiPolygonField(
                    srid=4326, null=True, blank=True, spatial_index=True,
                    help_text="Frontière du département (HDX ADM2).",
                )

    class Meta:
        verbose_name        = "Département"
        verbose_name_plural = "Départements"
        ordering            = ["name"]

    def __str__(self):
        return self.name


class SousPrefecture(models.Model):
    """ADM3 — Sous-préfecture (510). Plus petit échelon déconcentré de l'État."""

    code         = models.CharField(max_length=30, unique=True, verbose_name="Code")
    name         = models.CharField(max_length=100, verbose_name="Nom")
    departement  = models.ForeignKey(
                       Departement, on_delete=models.PROTECT, related_name="sous_prefectures",
                       verbose_name="Département",
                   )
    geom         = models.MultiPolygonField(
                       srid=4326, null=True, blank=True, spatial_index=True,
                       help_text="Frontière de la sous-préfecture (HDX ADM3).",
                   )

    class Meta:
        verbose_name        = "Sous-préfecture"
        verbose_name_plural = "Sous-préfectures"
        ordering            = ["name"]

    def __str__(self):
        return self.name


class Ville(models.Model):
    """
    Ville — collectivité urbaine qui regroupe une ou plusieurs communes.

    Niveau décentralisé : une Ville (ex. Abidjan) fédère ses communes
    (Cocody, Yopougon…). Pour une petite ville, Ville et Commune coïncident
    (1 commune). Rattachée obligatoirement à un District ; les niveaux
    Région / Département / Sous-préfecture sont nullable (mêmes raisons que
    Commune : districts autonomes, chevauchements).

    `geom` : polygone (union des communes membres ou frontière officielle),
    nullable au démarrage.
    """

    code             = models.CharField(max_length=30, unique=True, verbose_name="Code")
    name             = models.CharField(max_length=100, verbose_name="Nom")

    district         = models.ForeignKey(
                           District, on_delete=models.PROTECT, related_name="villes",
                           verbose_name="District",
                       )
    region           = models.ForeignKey(
                           Region, on_delete=models.PROTECT, related_name="villes",
                           null=True, blank=True, verbose_name="Région",
                       )
    departement      = models.ForeignKey(
                           Departement, on_delete=models.PROTECT, related_name="villes",
                           null=True, blank=True, verbose_name="Département",
                       )
    sous_prefecture  = models.ForeignKey(
                           SousPrefecture, on_delete=models.PROTECT, related_name="villes",
                           null=True, blank=True, verbose_name="Sous-préfecture",
                       )

    geom             = models.GeometryField(
                           srid=4326, null=True, blank=True, spatial_index=True,
                           help_text="Polygone (union des communes membres ou frontière officielle).",
                       )

    class Meta:
        verbose_name        = "Ville"
        verbose_name_plural = "Villes"
        ordering            = ["name"]

    def __str__(self):
        return self.name


class Commune(models.Model):
    """
    Commune (197) — collectivité locale décentralisée.

    Tous les niveaux de rattachement (District, Région, Département,
    Sous-préfecture, Ville) sont nullable : les communes sont importées
    seules depuis le GeoJSON (seule source de données actuelle), la
    hiérarchie sera raccrochée plus tard lors des imports BDR.

    `geom` peut être un Point (centroïde) ou un polygone. On utilise
    `GeometryField` générique pour accepter les deux formes.
    """

    code             = models.CharField(max_length=30, unique=True, verbose_name="Code")
    name             = models.CharField(max_length=100, verbose_name="Nom")

    district         = models.ForeignKey(
                           District, on_delete=models.PROTECT, related_name="communes",
                           null=True, blank=True, verbose_name="District",
                       )
    region           = models.ForeignKey(
                           Region, on_delete=models.PROTECT, related_name="communes",
                           null=True, blank=True, verbose_name="Région",
                       )
    departement      = models.ForeignKey(
                           Departement, on_delete=models.PROTECT, related_name="communes",
                           null=True, blank=True, verbose_name="Département",
                       )
    sous_prefecture  = models.ForeignKey(
                           SousPrefecture, on_delete=models.PROTECT, related_name="communes",
                           null=True, blank=True, verbose_name="Sous-préfecture",
                       )
    ville            = models.ForeignKey(
                           Ville, on_delete=models.PROTECT, related_name="communes",
                           null=True, blank=True, verbose_name="Ville",
                       )

    geom             = models.GeometryField(
                           srid=4326, null=True, blank=True, spatial_index=True,
                           help_text="Point (centroïde) au démarrage, polygone après import OSM (B.6).",
                       )

    class Meta:
        verbose_name        = "Commune"
        verbose_name_plural = "Communes"
        ordering            = ["name"]

    def __str__(self):
        return self.name
