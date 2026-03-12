import json
import subprocess
import sys
import os

from django.contrib import admin, messages
from django.http import HttpResponse
from django.utils.html import format_html

from .models import Zone, RoadSegment, FloodRisk, VegetationDensity, Alert


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS VISUELS
# ─────────────────────────────────────────────────────────────────────────────

def _badge(label, color, bg):
    return format_html(
        '<span style="padding:2px 10px;border-radius:12px;font-size:12px;'
        'font-weight:600;color:{};background:{}">{}</span>',
        color, bg, label
    )

def _score_badge(score):
    if score is None:
        return format_html('<span style="color:#94a3b8">—</span>')
    s = int(score)
    if s >= 70:   c, bg = '#16a34a', '#dcfce7'
    elif s >= 40: c, bg = '#d97706', '#fef3c7'
    else:         c, bg = '#dc2626', '#fee2e2'
    return format_html(
        '<span style="padding:2px 10px;border-radius:12px;font-size:12px;'
        'font-weight:600;color:{};background:{}">{}/100</span>',
        c, bg, s
    )

def _geojson_chip(geojson_data):
    if not geojson_data:
        return format_html('<span style="color:#94a3b8;font-style:italic">—</span>')
    geo_type = geojson_data.get('type', '?')
    coords   = geojson_data.get('coordinates', [])
    if geo_type == 'LineString':
        detail = f'{len(coords)} pts'
    elif geo_type == 'Polygon':
        detail = f'{len(coords[0]) if coords else 0} sommets'
    else:
        detail = ''
    return format_html(
        '<code style="font-size:11px;color:#3b82f6;background:#eff6ff;'
        'padding:2px 7px;border-radius:4px">{}{}</code>',
        geo_type, f' · {detail}' if detail else ''
    )


# ─────────────────────────────────────────────────────────────────────────────
# ZONE
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display       = ('name', 'code', 'lat_center', 'lng_center',
                          'nb_routes', 'nb_inondations', 'nb_alertes_actives',
                          'lien_dashboard')
    list_display_links = ('name', 'code')
    search_fields      = ('name', 'code')
    ordering           = ('name',)

    fieldsets = (
        ('Identification', {'fields': ('name', 'code', 'description')}),
        ('Centre de la carte', {
            'fields': ('lat_center', 'lng_center'),
            'description': 'Coordonnées WGS84. Ex : Man → lat 7.4125 / lng -7.5530',
        }),
    )

    @admin.display(description='Routes')
    def nb_routes(self, obj):
        n = obj.roads.count()
        return format_html('<b style="color:{}">{}</b>', '#16a34a' if n else '#94a3b8', n)

    @admin.display(description='Inondations')
    def nb_inondations(self, obj):
        n = obj.flood_risks.count()
        return format_html('<b style="color:{}">{}</b>', '#2563eb' if n else '#94a3b8', n)

    @admin.display(description='Alertes actives')
    def nb_alertes_actives(self, obj):
        n = obj.alerts.filter(is_read=False).count()
        return format_html('<b style="color:{}">{}</b>', '#dc2626' if n else '#94a3b8', n)

    @admin.display(description='')
    def lien_dashboard(self, obj):
        return format_html(
            '<a href="/?zone={}" target="_blank" '
            'style="padding:3px 12px;background:#3b82f6;color:#fff;'
            'border-radius:6px;font-size:12px;font-weight:600;text-decoration:none">Ouvrir</a>',
            obj.code
        )

    actions = ['importer_osm', 'vider_donnees']

    @admin.action(description='Importer depuis OpenStreetMap (routes + zones)')
    def importer_osm(self, request, queryset):
        ok, erreurs = [], []
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for zone in queryset:
            try:
                res = subprocess.run(
                    [sys.executable, 'manage.py', 'populate_geodata',
                     '--zone', zone.code, '--clear'],
                    capture_output=True, text=True, timeout=120, cwd=root
                )
                if res.returncode == 0:
                    ok.append(zone.name)
                else:
                    erreurs.append(f'{zone.name} — {(res.stderr or res.stdout)[:200]}')
            except subprocess.TimeoutExpired:
                erreurs.append(f'{zone.name} — timeout. Overpass lent ? Reessayez.')
            except Exception as e:
                erreurs.append(f'{zone.name} — {e}')
        if ok:
            self.message_user(request, f'Import termine : {", ".join(ok)}.', messages.SUCCESS)
        for err in erreurs:
            self.message_user(request, f'Erreur : {err}', messages.ERROR)

    @admin.action(description='Vider toutes les donnees de la zone')
    def vider_donnees(self, request, queryset):
        total = 0
        for zone in queryset:
            total += zone.roads.all().delete()[0]
            total += zone.flood_risks.all().delete()[0]
            total += zone.vegetation.all().delete()[0]
            total += zone.alerts.all().delete()[0]
        self.message_user(request, f'{total} objets supprimes.', messages.WARNING)


# ─────────────────────────────────────────────────────────────────────────────
# ROAD SEGMENT
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(RoadSegment)
class RoadSegmentAdmin(admin.ModelAdmin):
    list_display  = ('name', 'zone', 'surface_type',
                     'score_col', 'statut_col', 'last_analyzed', 'geo_col')
    list_filter   = ('zone', 'status', 'surface_type')
    search_fields = ('name', 'notes')
    ordering      = ('zone', '-condition_score')
    list_per_page = 50

    readonly_fields = ('apercu_geojson',)

    fieldsets = (
        ('Identification', {
            'fields': ('zone', 'name'),
        }),
        ('Surface', {
            'fields': ('surface_type',),
            'description': 'bitume | terre | pave | gravier | autre',
        }),
        ('Etat', {
            'fields': ('condition_score', 'status', 'last_analyzed', 'notes'),
            'description': (
                'condition_score : 0-100.  '
                'status : bon | degrade | critique | ferme.'
            ),
        }),
        ('Geometrie GeoJSON', {
            'fields': ('geojson', 'apercu_geojson'),
            'classes': ('collapse',),
            'description': '{"type": "LineString", "coordinates": [[lng, lat], [lng, lat], ...]}',
        }),
    )

    @admin.display(description='Score', ordering='condition_score')
    def score_col(self, obj):
        return _score_badge(obj.condition_score)

    @admin.display(description='Statut')
    def statut_col(self, obj):
        MAP = {
            'bon':      ('#16a34a', '#dcfce7', 'Bon'),
            'degrade':  ('#d97706', '#fef3c7', 'Degrade'),
            'critique': ('#dc2626', '#fee2e2', 'Critique'),
            'ferme':    ('#475569', '#f1f5f9', 'Ferme'),
        }
        c, bg, lbl = MAP.get(obj.status or '', ('#475569', '#f1f5f9', obj.status or '—'))
        return _badge(lbl, c, bg)

    @admin.display(description='GeoJSON')
    def geo_col(self, obj):
        return _geojson_chip(obj.geojson)

    @admin.display(description='Apercu GeoJSON')
    def apercu_geojson(self, obj):
        if not obj.geojson:
            return '—'
        raw = json.dumps(obj.geojson, indent=2, ensure_ascii=False)
        raw = raw[:3000] + ('\n... (tronque)' if len(raw) > 3000 else '')
        return format_html(
            '<pre style="font-size:11px;background:#0f172a;color:#94a3b8;'
            'padding:12px;border-radius:6px;max-height:320px;overflow:auto">{}</pre>',
            raw
        )

    actions = ['set_bon', 'set_degrade', 'set_critique', 'set_ferme', 'exporter_geojson']

    @admin.action(description='Bon etat (score 80)')
    def set_bon(self, request, qs):
        n = qs.update(status='bon', condition_score=80)
        self.message_user(request, f'{n} route(s) → Bon etat.', messages.SUCCESS)

    @admin.action(description='Degrade (score 45)')
    def set_degrade(self, request, qs):
        n = qs.update(status='degrade', condition_score=45)
        self.message_user(request, f'{n} route(s) → Degrade.', messages.WARNING)

    @admin.action(description='Critique (score 15)')
    def set_critique(self, request, qs):
        n = qs.update(status='critique', condition_score=15)
        self.message_user(request, f'{n} route(s) → Critique.', messages.ERROR)

    @admin.action(description='Ferme (score 0)')
    def set_ferme(self, request, qs):
        n = qs.update(status='ferme', condition_score=0)
        self.message_user(request, f'{n} route(s) → Fermee.', messages.WARNING)

    @admin.action(description='Exporter la selection en GeoJSON')
    def exporter_geojson(self, request, qs):
        features = [
            {
                'type': 'Feature',
                'geometry': r.geojson,
                'properties': {
                    'id':              r.pk,
                    'name':            r.name,
                    'zone':            r.zone.code if r.zone else None,
                    'surface_type':    r.surface_type,
                    'condition_score': r.condition_score,
                    'status':          r.status,
                }
            }
            for r in qs if r.geojson
        ]
        resp = HttpResponse(
            json.dumps({'type': 'FeatureCollection', 'features': features}, indent=2),
            content_type='application/json'
        )
        resp['Content-Disposition'] = 'attachment; filename="routes.geojson"'
        return resp


# ─────────────────────────────────────────────────────────────────────────────
# FLOOD RISK
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(FloodRisk)
class FloodRiskAdmin(admin.ModelAdmin):
    list_display  = ('name', 'zone', 'niveau_col', 'score_col',
                     'area_col', 'pluie_col', 'geo_col')
    list_filter   = ('zone', 'risk_level')
    search_fields = ('name',)
    ordering      = ('zone', '-risk_score')

    readonly_fields = ('apercu_geojson',)

    fieldsets = (
        ('Identification', {'fields': ('zone', 'name')}),
        ('Risque', {
            'fields': ('risk_level', 'risk_score'),
            'description': 'risk_level : faible | modere | eleve | critique',
        }),
        ('Donnees hydrologiques', {'fields': ('area_km2', 'rainfall_mm', 'last_analyzed')}),
        ('GeoJSON', {
            'fields': ('geojson', 'apercu_geojson'),
            'classes': ('collapse',),
            'description': '{"type": "Polygon", "coordinates": [[[lng,lat],...,[lng,lat]]]}',
        }),
    )

    RISK_C = {
        'faible':   ('#0891b2', '#e0f2fe', 'Faible'),
        'modere':   ('#2563eb', '#dbeafe', 'Modere'),
        'eleve':    ('#d97706', '#fef3c7', 'Eleve'),
        'critique': ('#dc2626', '#fee2e2', 'Critique'),
    }

    @admin.display(description='Niveau', ordering='risk_level')
    def niveau_col(self, obj):
        c, bg, lbl = self.RISK_C.get(obj.risk_level or '', ('#475569', '#f1f5f9', '—'))
        return _badge(lbl, c, bg)

    @admin.display(description='Score', ordering='risk_score')
    def score_col(self, obj):
        return _score_badge(obj.risk_score)

    @admin.display(description='Surface')
    def area_col(self, obj):
        return f'{obj.area_km2} km²' if obj.area_km2 else '—'

    @admin.display(description='Pluviometrie')
    def pluie_col(self, obj):
        return f'{obj.rainfall_mm} mm' if obj.rainfall_mm else '—'

    @admin.display(description='GeoJSON')
    def geo_col(self, obj):
        return _geojson_chip(obj.geojson)

    @admin.display(description='Apercu GeoJSON')
    def apercu_geojson(self, obj):
        if not obj.geojson:
            return '—'
        raw = json.dumps(obj.geojson, indent=2, ensure_ascii=False)[:3000]
        return format_html(
            '<pre style="font-size:11px;background:#0f172a;color:#94a3b8;'
            'padding:12px;border-radius:6px;max-height:320px;overflow:auto">{}</pre>', raw
        )

    actions = ['exporter_geojson']

    @admin.action(description='Exporter en GeoJSON')
    def exporter_geojson(self, request, qs):
        features = [
            {
                'type': 'Feature', 'geometry': z.geojson,
                'properties': {
                    'id': z.pk, 'name': z.name,
                    'risk_level': z.risk_level, 'risk_score': z.risk_score,
                    'area_km2': z.area_km2, 'rainfall_mm': z.rainfall_mm,
                }
            }
            for z in qs if z.geojson
        ]
        resp = HttpResponse(
            json.dumps({'type': 'FeatureCollection', 'features': features}, indent=2),
            content_type='application/json'
        )
        resp['Content-Disposition'] = 'attachment; filename="inondations.geojson"'
        return resp


# ─────────────────────────────────────────────────────────────────────────────
# VEGETATION DENSITY
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(VegetationDensity)
class VegetationDensityAdmin(admin.ModelAdmin):
    list_display  = ('name', 'zone', 'densite_col', 'ndvi_col',
                     'couverture_col', 'variation_col', 'geo_col')
    list_filter   = ('zone', 'density_class')
    search_fields = ('name',)
    ordering      = ('zone', '-ndvi_value')

    readonly_fields = ('apercu_geojson',)

    fieldsets = (
        ('Identification', {'fields': ('zone', 'name')}),
        ('Vegetation', {
            'fields': ('density_class', 'ndvi_value', 'coverage_percent',
                       'change_vs_previous', 'last_analyzed'),
            'description': (
                'density_class : sparse | moderate | dense | very_dense.  '
                'ndvi_value : -1 a 1.  '
                'change_vs_previous : variation NDVI depuis la derniere analyse '
                '(negatif = degradation).'
            ),
        }),
        ('GeoJSON', {
            'fields': ('geojson', 'apercu_geojson'),
            'classes': ('collapse',),
        }),
    )

    DENS_C = {
        'sparse':     ('#65a30d', '#f7fee7', 'Eparses'),
        'moderate':   ('#16a34a', '#dcfce7', 'Moderee'),
        'dense':      ('#15803d', '#bbf7d0', 'Dense'),
        'very_dense': ('#166534', '#86efac', 'Tres dense'),
    }

    @admin.display(description='Densite', ordering='density_class')
    def densite_col(self, obj):
        c, bg, lbl = self.DENS_C.get(obj.density_class or '', ('#475569', '#f1f5f9', '—'))
        return _badge(lbl, c, bg)

    @admin.display(description='NDVI', ordering='ndvi_value')
    def ndvi_col(self, obj):
        v   = obj.ndvi_value or 0
        pct = max(0, min(100, int((v + 1) / 2 * 100)))
        c, bg, _ = self.DENS_C.get(obj.density_class or '', ('#16a34a', '', ''))
        return format_html(
            '<div style="display:flex;align-items:center;gap:6px">'
            '<div style="width:72px;height:7px;background:#e2e8f0;border-radius:4px;overflow:hidden">'
            '<div style="width:{}%;height:100%;background:{};border-radius:4px"></div></div>'
            '<code style="font-size:11px">{}</code></div>',
            pct, c, f'{v:.3f}'
        )

    @admin.display(description='Couverture')
    def couverture_col(self, obj):
        return f'{obj.coverage_percent} %' if obj.coverage_percent is not None else '—'

    @admin.display(description='Variation NDVI')
    def variation_col(self, obj):
        # format_html ne supporte pas :.3f — on pré-formate le float
        v = obj.change_vs_previous or 0
        if v > 0:
            return format_html(
                '<span style="color:#16a34a;font-weight:600">+ {}</span>',
                f'{v:.3f}'
            )
        elif v < 0:
            return format_html(
                '<span style="color:#dc2626;font-weight:600">- {}</span>',
                f'{abs(v):.3f}'
            )
        return format_html('<span style="color:#94a3b8">0.000</span>')

    @admin.display(description='GeoJSON')
    def geo_col(self, obj):
        return _geojson_chip(obj.geojson)

    @admin.display(description='Apercu GeoJSON')
    def apercu_geojson(self, obj):
        if not obj.geojson:
            return '—'
        raw = json.dumps(obj.geojson, indent=2, ensure_ascii=False)[:3000]
        return format_html(
            '<pre style="font-size:11px;background:#0f172a;color:#94a3b8;'
            'padding:12px;border-radius:6px;max-height:320px;overflow:auto">{}</pre>', raw
        )


# ─────────────────────────────────────────────────────────────────────────────
# ALERT
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display   = ('titre_col', 'zone', 'categorie_col', 'severite_col',
                      'lu_col', 'created_at')
    list_filter    = ('zone', 'category', 'severity', 'is_read')
    search_fields  = ('title', 'message')
    ordering       = ('-created_at',)
    list_per_page  = 50
    date_hierarchy = 'created_at'

    readonly_fields = ('created_at', 'osm_link')

    fieldsets = (
        ('Contenu', {'fields': ('zone', 'title', 'message')}),
        ('Classification', {
            'fields': ('category', 'severity', 'is_read'),
            'description': (
                'category : road | flood | vegetation | system.  '
                'severity : info | warning | danger | critical.'
            ),
        }),
        ('Localisation', {
            'fields': ('lat', 'lng', 'osm_link'),
            'description': 'Si renseignees, le clic sur l\'alerte zoome sur ce point dans le dashboard.',
        }),
        ('Metadonnees', {'fields': ('created_at',)}),
    )

    SEV_C = {
        'info':     ('#0891b2', '#e0f2fe', 'INFO'),
        'warning':  ('#d97706', '#fef3c7', 'WARNING'),
        'danger':   ('#ea580c', '#ffedd5', 'DANGER'),
        'critical': ('#dc2626', '#fee2e2', 'CRITICAL'),
    }
    CAT_LABEL = {'road': 'Route', 'flood': 'Inondation', 'vegetation': 'Vegetation', 'system': 'Systeme'}

    @admin.display(description='Titre')
    def titre_col(self, obj):
        t = obj.title or ''
        return (t[:60] + '...') if len(t) > 60 else t

    @admin.display(description='Categorie')
    def categorie_col(self, obj):
        return self.CAT_LABEL.get(obj.category or '', obj.category or '—')

    @admin.display(description='Severite', ordering='severity')
    def severite_col(self, obj):
        c, bg, lbl = self.SEV_C.get(obj.severity or '', ('#475569', '#f1f5f9', '—'))
        return _badge(lbl, c, bg)

    @admin.display(description='Lu', boolean=True, ordering='is_read')
    def lu_col(self, obj):
        return obj.is_read

    @admin.display(description='Voir sur OSM')
    def osm_link(self, obj):
        if obj.lat and obj.lng:
            url = (f'https://www.openstreetmap.org/?mlat={obj.lat}'
                   f'&mlon={obj.lng}#map=16/{obj.lat}/{obj.lng}')
            return format_html(
                '<a href="{}" target="_blank">Ouvrir dans OpenStreetMap ({}, {})</a>',
                url, f'{obj.lat:.5f}', f'{obj.lng:.5f}'
            )
        return '—'

    actions = ['marquer_lues', 'marquer_non_lues', 'supprimer_lues']

    @admin.action(description='Marquer comme lues')
    def marquer_lues(self, request, qs):
        n = qs.update(is_read=True)
        self.message_user(request, f'{n} alerte(s) marquee(s) lues.', messages.SUCCESS)

    @admin.action(description='Marquer comme non lues')
    def marquer_non_lues(self, request, qs):
        n = qs.update(is_read=False)
        self.message_user(request, f'{n} alerte(s) remises en non lues.', messages.SUCCESS)

    @admin.action(description='Supprimer les alertes lues de la selection')
    def supprimer_lues(self, request, qs):
        n, _ = qs.filter(is_read=True).delete()
        self.message_user(request, f'{n} alerte(s) lue(s) supprimee(s).', messages.WARNING)