"""
Domaine « susceptibilité inondation » — calcul et persistance du score
d'une commune.

Extrait de la commande `update_flood_susceptibility` pour être réutilisable :
la commande en est désormais un client léger, et l'import de relevés terrain
(cible `flood_event`) rappelle le calcul sur les seules communes concernées.
Même pattern que `admin_divisions/communes.py` vis-à-vis d'`import_communes`.

⚠️ Coût : `get_physio_factors` interroge Earth Engine. Mesuré le 2026-07-20 à
**15,1 s par commune à froid** (0,01 s si le cache est chaud). Le cache est un
`LocMemCache` par processus — donc froid après chaque redémarrage. Ne jamais
appeler `recompute_communes` sur l'ensemble des communes depuis une requête
HTTP : c'est une opération de traitement par lot (~3,5 min pour les 14).
"""
import json

from django.utils import timezone

from admin_divisions.models import Commune
from flood import scoring
from flood.gee_factors import get_physio_factors
from flood.models import CommuneFloodSusceptibility, FloodEvent


def recompute_commune(commune):
    """
    Recalcule et enregistre la susceptibilité d'une commune.

    Renvoie le dict de `scoring.compute` (clés : susceptibility, physio,
    level, scores, raised_by_history), ou None si les facteurs GEE sont
    indisponibles — auquel cas RIEN n'est écrit (on ne remplace jamais un
    score existant par une valeur dégradée).
    """
    factors = get_physio_factors(json.loads(commune.geom.geojson))
    # Inondations observées intersectant la commune (0 si aucune) —
    # elles imposent un plancher, uniquement à la hausse.
    n_events = FloodEvent.objects.filter(geom__intersects=commune.geom).count()

    result = scoring.compute(factors, n_events) if factors else None
    if result is None:
        return None

    scores = result["scores"]
    CommuneFloodSusceptibility.objects.update_or_create(
        commune=commune,
        defaults={
            "elevation_mean_m":      factors.get("elevation_mean_m"),
            "elevation_min_m":       factors.get("elevation_min_m"),
            "slope_mean_deg":        factors.get("slope_mean_deg"),
            "hand_mean_m":           factors.get("hand_mean_m"),
            "hand_low_pct":          factors.get("hand_low_pct"),
            "flat_pct":              factors.get("flat_pct"),
            "built_low_pct":         factors.get("built_low_pct"),
            "history_events":        n_events,
            "urban_pct":             factors.get("urban_pct"),
            "water_pct":             factors.get("water_pct"),
            "score_hand_low":        scores.get("hand_low"),
            "score_exposure":        scores.get("exposure"),
            "score_elevation":       scores.get("elevation"),
            "score_impervious":      scores.get("impervious"),
            "score_water":           scores.get("water"),
            "score_flat":            scores.get("flat"),
            "physio_susceptibility": result["physio"],
            "susceptibility":        result["susceptibility"],
            "level":                 result["level"],
            "computed_at":           timezone.now(),
        },
    )
    result["history_events"] = n_events
    return result


def recompute_communes(queryset=None, log=lambda msg: None):
    """
    Recalcule un lot de communes. `queryset` par défaut : toutes celles qui
    ont une géométrie. Renvoie (ok, echecs) — `echecs` = liste de noms.

    Opération par lot, jamais depuis une requête HTTP (cf. avertissement de
    coût en tête de module).
    """
    qs = queryset if queryset is not None else Commune.objects.exclude(geom=None)
    ok, failed = 0, []
    for commune in qs.order_by("name"):
        result = recompute_commune(commune)
        if result is None:
            failed.append(commune.name)
            log(f"  ✗ {commune.name} : facteurs indisponibles (GEE ?)")
            continue
        ok += 1
        flag = "  ← relevé par l'historique" if result["raised_by_history"] else ""
        log(
            f"  ✓ {commune.name:14s} → {result['susceptibility']:5.1f}/100 "
            f"({result['level']:8s}) | terrain {result['physio']:5.1f} · "
            f"inondations observées {result['history_events']}{flag}"
        )
    return ok, failed


def communes_intersecting(geometries):
    """
    Communes recoupant au moins une des géométries fournies.

    Sert à ne recalculer QUE les communes réellement touchées par un import
    de relevés : 1 commune = 15 s, les 14 = 3,5 min.
    """
    qs = Commune.objects.none()
    for geom in geometries:
        if geom is None:
            continue
        qs = qs | Commune.objects.exclude(geom=None).filter(geom__intersects=geom)
    return qs.distinct()
