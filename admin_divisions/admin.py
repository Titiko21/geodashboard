"""
Admin Django pour les modèles admin_divisions.

Volontairement minimaliste à ce stade — on enrichira (inlines, actions,
choroplèthe miniature) au fur et à mesure que les imports peupleront les
tables. Tant qu'on n'a pas de donnée, un admin verbeux servirait à rien.
"""
from django.contrib import admin

from .models import Commune, Departement, District, Region, SousPrefecture, Ville


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display  = ("name", "code", "is_autonomous", "_nb_communes")
    list_filter   = ("is_autonomous",)
    search_fields = ("name", "code")
    ordering      = ("name",)

    @admin.display(description="Communes")
    def _nb_communes(self, obj):
        return obj.communes.count()


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display  = ("name", "code", "district")
    list_filter   = ("district",)
    search_fields = ("name", "code")
    ordering      = ("name",)


@admin.register(Departement)
class DepartementAdmin(admin.ModelAdmin):
    list_display  = ("name", "code", "region")
    list_filter   = ("region__district", "region")
    search_fields = ("name", "code")
    ordering      = ("name",)


@admin.register(SousPrefecture)
class SousPrefectureAdmin(admin.ModelAdmin):
    list_display  = ("name", "code", "departement")
    list_filter   = ("departement__region",)
    search_fields = ("name", "code")
    ordering      = ("name",)


@admin.register(Ville)
class VilleAdmin(admin.ModelAdmin):
    list_display  = ("name", "code", "district", "_nb_communes")
    list_filter   = ("district",)
    search_fields = ("name", "code")
    ordering      = ("name",)

    @admin.display(description="Communes")
    def _nb_communes(self, obj):
        return obj.communes.count()


@admin.register(Commune)
class CommuneAdmin(admin.ModelAdmin):
    list_display  = ("name", "code", "ville", "district", "sous_prefecture")
    list_filter   = ("district", "ville")
    search_fields = ("name", "code")
    ordering      = ("name",)
