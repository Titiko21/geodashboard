from django.contrib import admin

from flood.models import CommuneFloodSusceptibility


@admin.register(CommuneFloodSusceptibility)
class CommuneFloodSusceptibilityAdmin(admin.ModelAdmin):
    list_display = (
        "commune", "susceptibility", "level",
        "hand_mean_m", "elevation_mean_m", "slope_mean_deg",
        "urban_pct", "water_pct", "computed_at",
    )
    list_filter = ("level",)
    ordering = ("-susceptibility",)
    readonly_fields = ("computed_at", "sources")
