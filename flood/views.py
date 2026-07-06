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
                    "urban_pct":        r.urban_pct,
                    "water_pct":        r.water_pct,
                },
                "scores": {
                    "hand":       r.score_hand,
                    "elevation":  r.score_elevation,
                    "slope":      r.score_slope,
                    "impervious": r.score_impervious,
                    "water":      r.score_water,
                },
                "computed_at": r.computed_at.isoformat(),
                "sources":     r.sources,
            }
            for r in rows
        ],
    })
