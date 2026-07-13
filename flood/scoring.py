"""
Scoring de susceptibilité aux inondations — logique pure, testable sans DB.

v3 (2026-07-07) — cohérence avec la réalité observée.

Le score final combine DEUX choses, séparées et transparentes :

  1. SUSCEPTIBILITÉ PHYSIOGRAPHIQUE (le calcul terrain) — moyenne pondérée
     de 6 facteurs mesurés par satellite : bas-fonds, exposition du bâti,
     altitude, imperméabilisation, eau, planéité.

  2. PLANCHER OBSERVÉ (la réalité terrain) — les inondations réellement
     constatées imposent un score MINIMUM. « Inondé N fois » ⇒ « au moins
     tel niveau ». Ce plancher ne fait que MONTER le score, jamais baisser :
     l'absence d'observation ne prouve pas l'absence de risque (relevés
     incomplets), donc une commune non relevée garde sa valeur terrain.

  score_final = max(physiographique, plancher_observé)

Ainsi une commune historiquement inondée (Cocody, Bingerville…) ne peut plus
afficher un score faible, sans pour autant surestimer une commune lagunaire
non relevée (Marcory), dont le score reste porté par la physiographie.

Pondérations et seuils PROVISOIRES — à recalibrer avec plus d'observations,
la pluie (SODEXAM) et un MNT fin.
"""

# ── 1. Facteurs physiographiques (satellite) ────────────────────────────────

WEIGHTS = {
    "hand_low":   0.25,   # % du territoire en zone basse (HAND < 5 m)
    "exposure":   0.20,   # % du bâti situé en zone basse
    "elevation":  0.15,   # altitude (littoral bas)
    "impervious": 0.15,   # % bâti (imperméabilisation)
    "water":      0.15,   # % eau de surface
    "flat":       0.10,   # % quasi plat (< 2°)
}

LEVELS = [
    (25,  "faible"),
    (50,  "modere"),
    (75,  "eleve"),
    (101, "critique"),
]

# ── 2. Plancher imposé par les inondations OBSERVÉES ────────────────────────
# n événements constatés dans la commune ⇒ score au moins égal à ce plancher.
# Ne fait que monter le score (max), jamais baisser.
OBSERVED_FLOORS = [
    (10, 85),   # ≥ 10 observations → critique
    (6,  72),   # 6-9  → élevé haut
    (3,  58),   # 3-5  → élevé
    (1,  40),   # 1-2  → modéré haut
]


def _clamp(v):
    return round(max(0.0, min(100.0, v)), 1)


def score_hand_low(pct):
    return None if pct is None else _clamp(pct * 2.0)


def score_exposure(pct):
    return None if pct is None else _clamp(pct * 2.0)


def score_elevation(elevation_m):
    if elevation_m is None:
        return None
    if elevation_m <= 2.0:
        return 100.0
    if elevation_m >= 60.0:
        return 0.0
    return round(100.0 * (60.0 - elevation_m) / 58.0, 1)


def score_impervious(urban_pct):
    return None if urban_pct is None else _clamp(urban_pct)


def score_water(water_pct):
    return None if water_pct is None else _clamp(water_pct * 4.0)


def score_flat(pct):
    return None if pct is None else _clamp(pct)


_SCORERS = {
    "hand_low":   ("hand_low_pct",     score_hand_low),
    "exposure":   ("built_low_pct",    score_exposure),
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


def observed_floor(n_events):
    """Score minimum imposé par n inondations observées (0 si aucune)."""
    if not n_events:
        return 0.0
    for threshold, floor in OBSERVED_FLOORS:
        if n_events >= threshold:
            return float(floor)
    return 0.0


def compute_physio(factors):
    """
    Susceptibilité physiographique (0-100) = moyenne pondérée des 6 facteurs
    satellite. Les facteurs absents sont exclus et les poids renormalisés.

    Renvoie {"scores": {...}, "physio": float} ou None si aucun facteur.
    """
    scores = {
        name: scorer(factors.get(key))
        for name, (key, scorer) in _SCORERS.items()
    }
    available = {n: s for n, s in scores.items() if s is not None}
    if not available:
        return None
    total_weight = sum(WEIGHTS[n] for n in available)
    physio = round(
        sum(s * WEIGHTS[n] for n, s in available.items()) / total_weight, 1
    )
    return {"scores": scores, "physio": physio}


def combine(physio, n_events):
    """
    Score final = max(physiographique, plancher observé).
    Renvoie {"susceptibility", "floor", "level", "raised_by_history"}.
    """
    floor = observed_floor(n_events)
    final = round(max(physio, floor), 1)
    return {
        "susceptibility":    final,
        "floor":             floor,
        "level":             level_for(final),
        "raised_by_history": floor > physio,
    }


def compute(factors, n_events=0):
    """
    Calcul complet : physiographie + plancher observé.

    Renvoie {"scores", "physio", "susceptibility", "floor", "level",
    "raised_by_history"} ou None si aucun facteur physiographique.
    """
    base = compute_physio(factors)
    if base is None:
        return None
    result = combine(base["physio"], n_events)
    result["scores"] = base["scores"]
    result["physio"] = base["physio"]
    return result
