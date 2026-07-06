"""
Scoring de susceptibilité aux inondations — logique pure, testable sans DB.

Modèle à 2 couches (décision 2026-07-06) :
  1. SUSCEPTIBILITÉ STATIQUE (ce module) : croisement pondéré de facteurs
     physiographiques stables. C'est la couche implémentée en Phase C.
  2. Déclencheur dynamique (pluie SODEXAM/CHIRPS) : phase ultérieure —
     modulera la susceptibilité pour donner le risque opérationnel.

Facteurs v1 (résolution 30-90 m — susceptibilité de QUARTIER/COMMUNE ;
l'analyse par rue nécessitera un MNT fin ≤ 12 m) :

  hand        HAND (hauteur au-dessus du drainage, MERIT Hydro 90 m) —
              le meilleur prédicteur des zones basses accumulatrices.
  elevation   Altitude moyenne (Copernicus GLO-30) — zones côtières basses.
  slope       Pente moyenne — plate = évacuation lente.
  impervious  % bâti (Dynamic World) — imperméabilisation, ruissellement.
  water       % eau de surface (Dynamic World) — proximité plans d'eau.

PONDÉRATIONS PROVISOIRES (type AHP). À recalibrer avec la pluie SODEXAM,
la détection SAR et les observations terrain (Kobo) quand disponibles.
"""

WEIGHTS = {
    "hand":       0.35,
    "elevation":  0.15,
    "slope":      0.15,
    "impervious": 0.20,
    "water":      0.15,
}

LEVELS = [
    (25,  "faible"),
    (50,  "modere"),
    (75,  "eleve"),
    (101, "critique"),
]


def _linear_desc(value, at_100, at_0):
    """Score 0-100 décroissant linéairement : `at_100` → 100, `at_0` → 0."""
    if value is None:
        return None
    if value <= at_100:
        return 100.0
    if value >= at_0:
        return 0.0
    return round(100.0 * (at_0 - value) / (at_0 - at_100), 1)


def score_hand(hand_m):
    """0 m au-dessus du drainage → 100 ; ≥ 15 m → 0."""
    return _linear_desc(hand_m, 0.0, 15.0)


def score_elevation(elevation_m):
    """≤ 2 m d'altitude → 100 ; ≥ 60 m → 0 (littoral lagunaire d'Abidjan)."""
    return _linear_desc(elevation_m, 2.0, 60.0)


def score_slope(slope_deg):
    """Terrain plat (0°) → 100 ; pente ≥ 10° → 0."""
    return _linear_desc(slope_deg, 0.0, 10.0)


def score_impervious(urban_pct):
    """% bâti → score direct (imperméabilisation)."""
    if urban_pct is None:
        return None
    return round(max(0.0, min(100.0, urban_pct)), 1)


def score_water(water_pct):
    """% eau de surface, amplifié ×4 (25 % d'eau = exposition maximale)."""
    if water_pct is None:
        return None
    return round(max(0.0, min(100.0, water_pct * 4.0)), 1)


_SCORERS = {
    "hand":       score_hand,
    "elevation":  score_elevation,
    "slope":      score_slope,
    "impervious": score_impervious,
    "water":      score_water,
}


def level_for(score):
    for threshold, label in LEVELS:
        if score < threshold:
            return label
    return "critique"


def compute(factors):
    """
    Croise les facteurs bruts en un indice de susceptibilité 0-100.

    `factors` : dict avec (tous optionnels, None si indisponible)
        hand_mean_m, elevation_mean_m, slope_mean_deg, urban_pct, water_pct

    Les facteurs absents sont exclus et les pondérations renormalisées sur
    les facteurs disponibles (résultat toujours comparable 0-100).

    Renvoie {"scores": {facteur: sous-score|None}, "susceptibility": float,
             "level": str} — ou None si AUCUN facteur n'est disponible.
    """
    raw = {
        "hand":       factors.get("hand_mean_m"),
        "elevation":  factors.get("elevation_mean_m"),
        "slope":      factors.get("slope_mean_deg"),
        "impervious": factors.get("urban_pct"),
        "water":      factors.get("water_pct"),
    }
    scores = {name: _SCORERS[name](value) for name, value in raw.items()}

    available = {n: s for n, s in scores.items() if s is not None}
    if not available:
        return None

    total_weight = sum(WEIGHTS[n] for n in available)
    susceptibility = round(
        sum(s * WEIGHTS[n] for n, s in available.items()) / total_weight, 1
    )
    return {
        "scores": scores,
        "susceptibility": susceptibility,
        "level": level_for(susceptibility),
    }
