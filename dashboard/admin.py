from django.contrib import admin
from .models import Zone, RoadSegment, FloodRisk, VegetationDensity, Alert


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'lat_center', 'lng_center']
    search_fields = ['name', 'code']


@admin.register(RoadSegment)
class RoadSegmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'zone', 'status', 'surface_type', 'condition_score', 'last_analyzed']
    list_filter  = ['status', 'surface_type', 'zone']
    search_fields = ['name']
    ordering = ['condition_score']


@admin.register(FloodRisk)
class FloodRiskAdmin(admin.ModelAdmin):
    list_display = ['name', 'zone', 'risk_level', 'risk_score', 'area_km2', 'rainfall_mm']
    list_filter  = ['risk_level', 'zone']
    ordering = ['-risk_score']


@admin.register(VegetationDensity)
class VegetationDensityAdmin(admin.ModelAdmin):
    list_display = ['name', 'zone', 'ndvi_value', 'density_class', 'coverage_percent', 'change_vs_previous']
    list_filter  = ['density_class', 'zone']


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display   = ['title', 'severity', 'category', 'zone', 'is_read', 'created_at']
    list_filter    = ['severity', 'category', 'is_read', 'zone']
    list_editable  = ['is_read']
    ordering       = ['-created_at']
    actions        = ['mark_as_read']

    @admin.action(description="Marquer comme lues")
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)
