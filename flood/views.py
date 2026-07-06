from django.http import JsonResponse

from flood.models import CommuneFloodSusceptibility
from flood.scoring import WEIGHTS


def api_flood_susceptibility(request):
    """
    GET /api/flood/susceptibility/

    Susceptibilité aux inondations par commune (indice statique 0-100,
    facteurs bruts et sous-scores inclus — transparence du calcul).
    """
    rows = (
        CommuneFloodSusceptibility.objects
        .select_related("commune")
        .order_by("-susceptibility")
    )
    return JsonResponse({
        "weights": WEIGHTS,
        "communes": [
            {
                "code":           r.commune.code,
                "name":           r.commune.name,
                "susceptibility": r.susceptibility,
                "level":          r.level,
                "level_label":    r.get_level_display(),
                "factors": {
                    "elevation_mean_m": r.elevation_mean_m,
                    "elevation_min_m":  r.elevation_min_m,
                    "slope_mean_deg":   r.slope_mean_deg,
                    "hand_mean_m":      r.hand_mean_m,
                    "hand_low_pct":     r.hand_low_pct,
                    "flat_pct":         r.flat_pct,
                    "built_low_pct":    r.built_low_pct,
                    "history_events":   r.history_events,
                    "urban_pct":        r.urban_pct,
                    "water_pct":        r.water_pct,
                },
                "scores": {
                    "hand_low":   r.score_hand_low,
                    "exposure":   r.score_exposure,
                    "history":    r.score_history,
                    "elevation":  r.score_elevation,
                    "impervious": r.score_impervious,
                    "water":      r.score_water,
                    "flat":       r.score_flat,
                },
                "computed_at": r.computed_at.isoformat(),
                "sources":     r.sources,
            }
            for r in rows
        ],
    })
