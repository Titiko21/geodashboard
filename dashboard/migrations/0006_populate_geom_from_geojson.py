"""
Migration 0006 — Peuplement des `geom` à partir des `geojson` existants.

Parse chaque GeoJSON stocké en JSONField et l'écrit dans la GeometryField
PostGIS correspondante. Les `geojson` restent intacts (source de vérité
pendant la transition).

Robustesse :
  - Validation structurelle préalable (LineString >= 2 pts, Polygon ring >= 4 pts,
    rings auto-fermés si nécessaire).
  - Tente bulk_update → fallback row-by-row si un INSERT bulk plante,
    pour isoler les géométries que PostGIS rejette malgré la pré-validation.
  - Idempotence : skip silencieux des lignes déjà peuplées (utile en rerun).
  - atomic=False : chaque batch commit séparément ; relancer reprend
    naturellement là où ça s'était arrêté.

Reverse : SET geom = NULL sur les 3 tables (les `geojson` ne sont pas touchés).
"""
import json

from django.db import migrations, transaction


BATCH = 500
TARGET_MODELS = ['RoadSegment', 'FloodRisk', 'VegetationDensity']


def _close_ring(ring):
    """Ferme un ring (premier point == dernier point) si nécessaire."""
    if isinstance(ring, list) and len(ring) >= 1 and ring[0] != ring[-1]:
        return ring + [ring[0]]
    return ring


def _validate_geojson_shape(geojson_value):
    """
    Valide la structure GeoJSON et ferme les rings de polygones si besoin.
    Retourne le dict normalisé prêt à être parsé, ou None si invalide.
    """
    if not geojson_value or not isinstance(geojson_value, dict):
        return None

    gtype = geojson_value.get("type")
    coords = geojson_value.get("coordinates")
    if not gtype or coords is None:
        return None

    if gtype == "LineString":
        if not isinstance(coords, list) or len(coords) < 2:
            return None
        return geojson_value

    if gtype == "MultiLineString":
        if not isinstance(coords, list) or not coords:
            return None
        for line in coords:
            if not isinstance(line, list) or len(line) < 2:
                return None
        return geojson_value

    if gtype == "Polygon":
        if not isinstance(coords, list) or not coords:
            return None
        closed_rings = []
        for ring in coords:
            if not isinstance(ring, list):
                return None
            ring = _close_ring(ring)
            if len(ring) < 4:
                return None
            closed_rings.append(ring)
        return {"type": "Polygon", "coordinates": closed_rings}

    if gtype == "MultiPolygon":
        if not isinstance(coords, list) or not coords:
            return None
        fixed = []
        for polygon in coords:
            if not isinstance(polygon, list) or not polygon:
                return None
            rings = []
            for ring in polygon:
                if not isinstance(ring, list):
                    return None
                ring = _close_ring(ring)
                if len(ring) < 4:
                    return None
                rings.append(ring)
            fixed.append(rings)
        return {"type": "MultiPolygon", "coordinates": fixed}

    return None


def _parse_geojson_to_geom(geojson_value):
    """Convertit un GeoJSON en GEOSGeometry SRID 4326, ou None si invalide."""
    from django.contrib.gis.geos import GEOSGeometry

    normalized = _validate_geojson_shape(geojson_value)
    if normalized is None:
        return None
    try:
        g = GEOSGeometry(json.dumps(normalized), srid=4326)
        if g.empty:
            return None
        return g
    except Exception:
        return None


def _persist_batch(Model, to_update, counters):
    """Tente bulk_update ; si ça plante, retombe en row-by-row pour isoler."""
    if not to_update:
        return
    try:
        with transaction.atomic():
            Model.objects.bulk_update(to_update, ['geom'])
        counters['valid'] += len(to_update)
        return
    except Exception:
        # Fallback row-by-row pour identifier les géométries rejetées par PostGIS.
        for obj in to_update:
            try:
                with transaction.atomic():
                    Model.objects.filter(pk=obj.pk).update(geom=obj.geom)
                counters['valid'] += 1
            except Exception:
                counters['invalid'] += 1


def populate_forward(apps, schema_editor):
    for model_name in TARGET_MODELS:
        Model = apps.get_model('dashboard', model_name)
        total = Model.objects.count()
        counters = {'valid': 0, 'invalid': 0, 'already_ok': 0}
        last_id = 0

        while True:
            batch = list(
                Model.objects
                .filter(id__gt=last_id)
                .only('id', 'geojson', 'geom')
                .order_by('id')[:BATCH]
            )
            if not batch:
                break

            to_update = []
            for obj in batch:
                if obj.geom is not None:
                    counters['already_ok'] += 1
                    continue
                geom = _parse_geojson_to_geom(obj.geojson)
                if geom is None:
                    counters['invalid'] += 1
                    continue
                obj.geom = geom
                to_update.append(obj)

            _persist_batch(Model, to_update, counters)
            last_id = batch[-1].id

        print(
            f"[0006] {model_name}: {counters['valid']} peuplées, "
            f"{counters['already_ok']} déjà OK, {counters['invalid']} ignorées "
            f"({total} total)"
        )


def populate_reverse(apps, schema_editor):
    for model_name in TARGET_MODELS:
        Model = apps.get_model('dashboard', model_name)
        n = Model.objects.update(geom=None)
        print(f"[0006-reverse] {model_name}: {n} lignes remises à geom=NULL")


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('dashboard', '0005_add_geometry_fields'),
    ]

    operations = [
        migrations.RunPython(populate_forward, populate_reverse),
    ]
