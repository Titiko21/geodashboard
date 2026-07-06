"""
Scoring de susceptibilité aux inondations — logique pure, testable sans DB.

v2 (2026-07-06) — corrige la dilution par moyenne communale :
  Le v1 moyennait altitude/HAND/pente sur TOUTE la commune : les bas-fonds
  urbanisés (vallées de Cocody…) se diluaient dans le plateau. Le v2 score
  des FRACTIONS de territoire et l'EXPOSITION du bâti, et intègre
  l'HISTORIQUE des inondations observées (couche FloodEvent importable).

Facteurs v2 :
  hand_low    % du territoire en zone basse (HAND < 5 m) — bas-fonds.
  exposure    % du BÂTI situé en zone basse — quartiers construits dans
              les vallées/bas-fonds (le facteur « Cocody »).
  history     Événements d'inondation observés dans la commune
              (couche FloodEvent — vérité terrain, importable).
  elevation   Altitude moyenne — communes littorales basses.
  impervious  % bâti — imperméabilisation, ruissellement.
  water       % eau de surface — proximité plans d'eau.
  flat        % du territoire quasi plat (pente < 2°) — évacuation lente.

PONDÉRATIONS PROVISOIRES (somme = 1 quand tout est disponible ; les
facteurs absents sont exclus et les poids renormalisés). À recalibrer
avec la pluie SODEXAM et les relevés terrain.
"""

WEIGHTS = {
    "hand_low":   0.20,
    "exposure":   0.17,
    "history":    0.20,
    "elevation":  0.12,
    "impervious": 0.12,
    "water":      0.12,
    "flat":       0.07,
}

LEVELS = [
    (25,  "faible"),
    (50,  "modere"),
    (75,  "eleve"),
    (101, "critique"),
]


def _clamp(v):
    return round(max(0.0, min(100.0, v)), 1)


def score_hand_low(pct):
    """% de territoire en zone basse, amplifié ×2 (50 % de bas-fonds = max)."""
    return None if pct is None else _clamp(pct * 2.0)


def score_exposure(pct):
    """% du bâti en zone basse, amplifié ×2 (50 % du bâti exposé = max)."""
    return None if pct is None else _clamp(pct * 2.0)


def score_history(n_events):
    """Événements observés : 1 → 25 … 4+ → 100. Vérité terrain prioritaire."""
    return None if n_events is None else _clamp(n_events * 25.0)


def score_elevation(elevation_m):
    """≤ 2 m d'altitude → 100 ; ≥ 60 m → 0 (littoral lagunaire d'Abidjan)."""
    if elevation_m is None:
        return None
    if elevation_m <= 2.0:
        return 100.0
    if elevation_m >= 60.0:
        return 0.0
    return round(100.0 * (60.0 - elevation_m) / 58.0, 1)


def score_impervious(urban_pct):
    """% bâti → score direct (imperméabilisation)."""
    return None if urban_pct is None else _clamp(urban_pct)


def score_water(water_pct):
    """% eau de surface, amplifié ×4 (25 % d'eau = exposition maximale)."""
    return None if water_pct is None else _clamp(water_pct * 4.0)


def score_flat(pct):
    """% du territoire quasi plat (< 2°) → score direct."""
    return None if pct is None else _clamp(pct)


_SCORERS = {
    "hand_low":   ("hand_low_pct",     score_hand_low),
    "exposure":   ("built_low_pct",    score_exposure),
    "history":    ("history_events",   score_history),
    "elevation":  ("elevation_mean_m", score_elevation),
    "impervious": ("urban_pct",        score_impervious),
    "water":      ("water_pct",        score_water),
    "flat":       ("flat_pct",         score_flat),
}


def level_for(score):
    for threshold, label in LEVELS:
        if score < threshold:
            return label
    return "critique"


def compute(factors):
    """
    Croise les facteurs bruts en un indice de susceptibilité 0-100.

    `factors` : dict (clés cf. _SCORERS, valeurs None si indisponibles).
    Les facteurs absents sont exclus et les pondérations renormalisées.

    Renvoie {"scores": {...}, "susceptibility": float, "level": str},
    ou None si AUCUN facteur n'est disponible.
    """
    scores = {
        name: scorer(factors.get(key))
        for name, (key, scorer) in _SCORERS.items()
    }
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
