import csv
import json

from django.contrib import admin
from django.http import HttpResponse

from flood.models import CommuneFloodSusceptibility, FloodEvent


def _export_csv(queryset, fields, filename):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow(fields)
    for obj in queryset:
        w.writerow([getattr(obj, f, "") for f in fields])
    return resp


@admin.register(CommuneFloodSusceptibility)
class CommuneFloodSusceptibilityAdmin(admin.ModelAdmin):
    list_display = (
        "commune", "susceptibility", "level",
        "hand_low_pct", "built_low_pct", "history_events",
        "urban_pct", "computed_at",
    )
    list_filter = ("level",)
    ordering = ("-susceptibility",)
    readonly_fields = ("computed_at", "sources")
    actions = ("export_csv", "export_geojson")

    @admin.action(description="Exporter la sélection en CSV")
    def export_csv(self, request, queryset):
        rows = queryset.select_related("commune")
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="susceptibilite_inondation.csv"'
        w = csv.writer(resp)
        w.writerow(["commune", "code", "susceptibilite", "niveau",
                    "hand_low_pct", "built_low_pct", "flat_pct", "history_events",
                    "elevation_mean_m", "hand_mean_m", "urban_pct", "water_pct",
                    "calcule_le"])
        for r in rows:
            w.writerow([r.commune.name, r.commune.code, r.susceptibility, r.level,
                        r.hand_low_pct, r.built_low_pct, r.flat_pct, r.history_events,
                        r.elevation_mean_m, r.hand_mean_m, r.urban_pct, r.water_pct,
                        r.computed_at.isoformat()])
        return resp

    @admin.action(description="Exporter la sélection en GeoJSON (polygones communes)")
    def export_geojson(self, request, queryset):
        features = []
        for r in queryset.select_related("commune"):
            if r.commune.geom is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": json.loads(r.commune.geom.geojson),
                "properties": {
                    "commune": r.commune.name, "code": r.commune.code,
                    "susceptibility": r.susceptibility, "level": r.level,
                    "hand_low_pct": r.hand_low_pct, "built_low_pct": r.built_low_pct,
                    "history_events": r.history_events,
                },
            })
        resp = HttpResponse(
            json.dumps({"type": "FeatureCollection", "features": features}),
            content_type="application/geo+json",
        )
        resp["Content-Disposition"] = 'attachment; filename="susceptibilite_inondation.geojson"'
        return resp


@admin.register(FloodEvent)
class FloodEventAdmin(admin.ModelAdmin):
    list_display = ("name", "date", "source", "code")
    search_fields = ("name", "source")
    list_filter = ("date",)
    actions = ("export_geojson",)

    @admin.action(description="Exporter la sélection en GeoJSON")
    def export_geojson(self, request, queryset):
        features = [{
            "type": "Feature",
            "geometry": json.loads(e.geom.geojson),
            "properties": {"name": e.name, "date": e.date.isoformat() if e.date else None,
                           "source": e.source},
        } for e in queryset]
        resp = HttpResponse(
            json.dumps({"type": "FeatureCollection", "features": features}),
            content_type="application/geo+json",
        )
        resp["Content-Disposition"] = 'attachment; filename="evenements_inondation.geojson"'
        return resp
