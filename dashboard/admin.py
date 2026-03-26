"""
GéoDash — admin.py
Interface d'administration propre et fonctionnelle.
Basé sur les champs réels de models.py.
"""

from django.contrib import admin
from django.db.models import Avg, Count, Min, Max, Q
from django.urls import reverse
from django.utils.html import format_html
from django.contrib import messages

from .models import Zone, RoadSegment, FloodRisk, VegetationDensity, Alert


# ── En-tête du site ───────────────────────────────────────────────────────────

admin.site.site_header = "GéoDash — Administration"
admin.site.site_title  = "GéoDash"
admin.site.index_title = "Gestion des données géospatiales"


# ══════════════════════════════════════════════════════════
#  PROXY — renommage "Sections routières" sans toucher models.py
# ══════════════════════════════════════════════════════════

class RoadSection(RoadSegment):
    """Proxy pour afficher 'Section routière' dans l'admin."""
    class Meta:
        proxy               = True
        verbose_name        = "Section routière"
        verbose_name_plural = "Sections routières"


# ══════════════════════════════════════════════════════════
#  INLINES
# ══════════════════════════════════════════════════════════

class RoadSectionInline(admin.TabularInline):
    """10 sections les plus dégradées dans la fiche d'une Zone."""
    model               = RoadSegment
    extra               = 0
    max_num             = 10
    can_delete          = False
    show_change_link    = True
    verbose_name        = "Section dégradée"
    verbose_name_plural = "Sections les plus dégradées (score < 50)"
    fields              = ('name', 'surface_type', 'condition_score', 'status', 'last_analyzed')
    readonly_fields     = ('name', 'surface_type', 'condition_score', 'status', 'last_analyzed')

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .filter(condition_score__lt=50)
            .order_by('condition_score')
        )


class AlertInline(admin.TabularInline):
    """Alertes non lues dans la fiche d'une Zone."""
    model               = Alert
    extra               = 0
    max_num             = 10
    can_delete          = True
    show_change_link    = True
    verbose_name        = "Alerte active"
    verbose_name_plural = "Alertes actives (non lues)"
    fields              = ('title', 'severity', 'category', 'created_at')
    readonly_fields     = ('title', 'severity', 'category', 'created_at')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_read=False).order_by('-created_at')


class FloodRiskInline(admin.TabularInline):
    """Zones inondation à risque élevé ou critique."""
    model               = FloodRisk
    extra               = 0
    max_num             = 10
    can_delete          = False
    show_change_link    = True
    verbose_name        = "Zone à risque"
    verbose_name_plural = "Zones à risque élevé / critique"
    fields              = ('name', 'risk_level', 'risk_score', 'area_km2', 'rainfall_mm')
    readonly_fields     = ('name', 'risk_level', 'risk_score', 'area_km2', 'rainfall_mm')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(risk_level__in=['eleve', 'critique'])


# ══════════════════════════════════════════════════════════
#  ZONE
# ══════════════════════════════════════════════════════════

@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):

    list_display  = ('name', 'code', 'nb_sections', 'score_moyen', 'nb_alertes', 'nb_inondations')
    search_fields = ('name', 'code')
    ordering      = ('name',)
    list_per_page = 50

    fieldsets = (
        ("Identification", {
            'fields': (('name', 'code'), 'description'),
        }),
        ("Position sur la carte", {
            'description': (
                "Centroïde utilisé pour le recentrage de la carte "
                "et comme coordonnée de fallback pour les alertes sans position précise."
            ),
            'fields': (('lat_center', 'lng_center'),),
        }),
        ("Synthèse (calculée automatiquement)", {
            'classes': ('collapse',),
            'fields':  ('_synthese',),
        }),
    )
    readonly_fields = ('_synthese',)
    inlines         = [AlertInline, RoadSectionInline, FloodRiskInline]

    # ── Colonnes ──────────────────────────────────────────

    @admin.display(description="Sections", ordering='_nb_sections')
    def nb_sections(self, obj):
        n   = getattr(obj, '_nb_sections', RoadSegment.objects.filter(zone=obj).count())
        url = reverse('admin:dashboard_roadsection_changelist') + f'?zone__id__exact={obj.pk}'
        return format_html('<a href="{}">{}</a>', url, n)

    @admin.display(description="Score moy.")
    def score_moyen(self, obj):
        avg = RoadSegment.objects.filter(zone=obj).aggregate(v=Avg('condition_score'))['v']
        return f"{avg:.1f} / 100" if avg is not None else "—"

    @admin.display(description="Alertes actives")
    def nb_alertes(self, obj):
        n   = Alert.objects.filter(zone=obj, is_read=False).count()
        url = reverse('admin:dashboard_alert_changelist') + f'?zone__id__exact={obj.pk}&is_read__exact=0'
        return format_html('<a href="{}">{}</a>', url, n) if n else "0"

    @admin.display(description="Zones inondation")
    def nb_inondations(self, obj):
        n   = FloodRisk.objects.filter(zone=obj).count()
        url = reverse('admin:dashboard_floodrisk_changelist') + f'?zone__id__exact={obj.pk}'
        return format_html('<a href="{}">{}</a>', url, n) if n else "0"

    @admin.display(description="Synthèse")
    def _synthese(self, obj):
        if not obj.pk:
            return "Sauvegardez d'abord la zone."

        roads  = RoadSegment.objects.filter(zone=obj)
        floods = FloodRisk.objects.filter(zone=obj)
        alerts = Alert.objects.filter(zone=obj, is_read=False)

        stats = roads.aggregate(
            total=Count('id'),
            avg=Avg('condition_score'),
            min=Min('condition_score'),
            max=Max('condition_score'),
            critiques=Count('id', filter=Q(condition_score__lt=40)),
        )

        rows = [
            ("Sections routières",              stats['total'] or 0),
            ("Score moyen",                     f"{stats['avg']:.1f} / 100" if stats['avg'] else "—"),
            ("Score min / max",                 f"{stats['min']:.0f} / {stats['max']:.0f}" if stats['min'] is not None else "—"),
            ("Sections critiques (score < 40)", stats['critiques'] or 0),
            ("Zones d'inondation",              floods.count()),
            ("Risque élevé ou critique",        floods.filter(risk_level__in=['eleve', 'critique']).count()),
            ("Alertes actives",                 alerts.count()),
            ("Alertes critiques non lues",      alerts.filter(severity='critical').count()),
        ]

        html = '<table style="border-collapse:collapse;font-size:13px;width:360px">'
        for label, val in rows:
            html += (
                f'<tr>'
                f'<td style="padding:5px 20px 5px 0;color:#555">{label}</td>'
                f'<td style="padding:5px 0;font-weight:600">{val}</td>'
                f'</tr>'
            )
        html += '</table>'
        return format_html(html)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _nb_sections=Count('roads', distinct=True)
        )


# ══════════════════════════════════════════════════════════
#  SECTION ROUTIÈRE (proxy de RoadSegment)
# ══════════════════════════════════════════════════════════

@admin.register(RoadSection)
class RoadSectionAdmin(admin.ModelAdmin):

    list_display   = (
        'name', 'zone', 'surface_type',
        'condition_score', 'status', 'last_analyzed',
    )
    list_filter    = ('zone', 'status', 'surface_type')
    search_fields  = ('name', 'zone__name', 'notes')
    ordering       = ('condition_score',)
    list_per_page  = 50
    date_hierarchy = 'last_analyzed'

    fieldsets = (
        ("Identification", {
            'fields': (('name', 'zone'),),
        }),
        ("État de la section", {
            'description': (
                "Le score est calculé automatiquement lors de l'import OSM "
                "(tags : highway, surface, smoothness). "
                "Modifiez-le manuellement uniquement après inspection terrain."
            ),
            'fields': (
                ('condition_score', 'status'),
                'surface_type',
                'notes',
            ),
        }),
        ("Référence OpenStreetMap", {
            'description': "Identifiant OSM — clé de mise à jour lors des imports automatiques.",
            'fields': ('osm_id',),
        }),
        ("Géométrie (tracé)", {
            'description': "GeoJSON LineString du tracé routier, issu de l'API Overpass. Ne pas modifier manuellement.",
            'classes': ('collapse',),
            'fields':  ('geojson',),
        }),
        ("Horodatage", {
            'classes': ('collapse',),
            'fields':  ('last_analyzed',),
        }),
    )

    actions = ['action_mark_bon', 'action_mark_critique']

    @admin.action(description="Marquer sélection comme Bon état (score 80)")
    def action_mark_bon(self, request, queryset):
        n = queryset.update(condition_score=80, status='bon')
        self.message_user(request, f"{n} section(s) marquées « Bon état ».", messages.SUCCESS)

    @admin.action(description="Marquer sélection comme Critique (score 15)")
    def action_mark_critique(self, request, queryset):
        n = queryset.update(condition_score=15, status='critique')
        self.message_user(request, f"{n} section(s) marquées « Critique ».", messages.WARNING)


# ══════════════════════════════════════════════════════════
#  ZONE D'INONDATION
# ══════════════════════════════════════════════════════════

@admin.register(FloodRisk)
class FloodRiskAdmin(admin.ModelAdmin):

    list_display   = (
        'name', 'zone', 'risk_level', 'risk_score',
        'area_km2', 'rainfall_mm', 'last_analyzed',
    )
    list_filter    = ('zone', 'risk_level')
    search_fields  = ('name', 'zone__name')
    ordering       = ('-risk_score',)
    list_per_page  = 50
    date_hierarchy = 'last_analyzed'

    fieldsets = (
        ("Identification", {
            'fields': (('name', 'zone'),),
        }),
        ("Évaluation du risque", {
            'description': (
                "Score de 0 à 100 — détermine automatiquement le niveau : "
                "0-24 = Faible · 25-49 = Modéré · 50-74 = Élevé · 75-100 = Critique."
            ),
            'fields': (
                ('risk_score', 'risk_level'),
                ('area_km2', 'rainfall_mm'),
            ),
        }),
        ("Référence OpenStreetMap", {
            'description': "Identifiant OSM — clé de mise à jour lors des imports automatiques.",
            'fields': ('osm_id',),
        }),
        ("Géométrie (périmètre)", {
            'classes': ('collapse',),
            'fields':  ('geojson',),
        }),
        ("Horodatage", {
            'classes': ('collapse',),
            'fields':  ('last_analyzed',),
        }),
    )


# ══════════════════════════════════════════════════════════
#  VÉGÉTATION
# ══════════════════════════════════════════════════════════

@admin.register(VegetationDensity)
class VegetationDensityAdmin(admin.ModelAdmin):

    list_display   = (
        'name', 'zone', 'density_class',
        'ndvi_value', 'coverage_percent',
        'change_vs_previous', 'last_analyzed',
    )
    list_filter    = ('zone', 'density_class')
    search_fields  = ('name', 'zone__name')
    ordering       = ('-ndvi_value',)
    list_per_page  = 50
    date_hierarchy = 'last_analyzed'

    fieldsets = (
        ("Identification", {
            'fields': (('name', 'zone'),),
        }),
        ("Indice NDVI", {
            'description': (
                "Normalized Difference Vegetation Index — de -1 à +1. "
                "Classes : < 0.20 = Éparse · 0.20-0.39 = Modérée · "
                "0.40-0.59 = Dense · ≥ 0.60 = Très dense."
            ),
            'fields': (
                ('ndvi_value', 'density_class'),
                ('coverage_percent', 'change_vs_previous'),
            ),
        }),
        ("Référence OpenStreetMap", {
            'description': "Identifiant OSM — clé de mise à jour lors des imports automatiques.",
            'fields': ('osm_id',),
        }),
        ("Géométrie (périmètre)", {
            'classes': ('collapse',),
            'fields':  ('geojson',),
        }),
        ("Horodatage", {
            'classes': ('collapse',),
            'fields':  ('last_analyzed',),
        }),
    )


# ══════════════════════════════════════════════════════════
#  ALERTE
# ══════════════════════════════════════════════════════════

class AlertReadFilter(admin.SimpleListFilter):
    title          = "Statut de lecture"
    parameter_name = "read_status"

    def lookups(self, request, model_admin):
        return [('unread', 'Non lues'), ('read', 'Lues')]

    def queryset(self, request, queryset):
        if self.value() == 'unread':
            return queryset.filter(is_read=False)
        if self.value() == 'read':
            return queryset.filter(is_read=True)
        return queryset


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):

    list_display   = (
        'title', 'zone', 'severity', 'category',
        'is_read', 'coords_display', 'created_at',
    )
    list_filter    = (AlertReadFilter, 'severity', 'category', 'zone')
    search_fields  = ('title', 'message', 'zone__name')
    ordering       = ('-created_at',)
    list_per_page  = 50
    date_hierarchy = 'created_at'

    fieldsets = (
        ("Contenu", {
            'fields': (
                ('title', 'severity'),
                ('category', 'zone'),
                'message',
            ),
        }),
        ("Localisation sur la carte", {
            'description': (
                "Coordonnées GPS exactes du point d'alerte. "
                "Si non renseignées, le dashboard utilise le centroïde "
                "de la zone (lat_center / lng_center) comme position de fallback."
            ),
            'fields': (('lat', 'lng'),),
        }),
        ("Gestion", {
            'fields': (('is_read', 'created_at'),),
        }),
    )
    readonly_fields = ('created_at',)

    actions = ['action_mark_read', 'action_mark_unread']

    @admin.action(description="Marquer comme lues")
    def action_mark_read(self, request, queryset):
        n = queryset.update(is_read=True)
        self.message_user(request, f"{n} alerte(s) marquées comme lues.", messages.SUCCESS)

    @admin.action(description="Marquer comme non lues")
    def action_mark_unread(self, request, queryset):
        n = queryset.update(is_read=False)
        self.message_user(request, f"{n} alerte(s) remises en non lues.", messages.SUCCESS)

    @admin.display(description="Coordonnées")
    def coords_display(self, obj):
        if obj.lat and obj.lng:
            return f"{obj.lat:.4f}, {obj.lng:.4f}"
        return "— (fallback zone)"