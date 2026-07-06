"""
Tests du scoring de susceptibilité inondation v2 — logique pure, sans DB ni GEE.
"""
from flood.scoring import (
    WEIGHTS,
    compute,
    level_for,
    score_elevation,
    score_exposure,
    score_flat,
    score_hand_low,
    score_history,
    score_impervious,
    score_water,
)


class TestSubscores:
    def test_hand_low_amplified(self):
        assert score_hand_low(0.0) == 0.0
        assert score_hand_low(25.0) == 50.0    # 25 % de bas-fonds → 50
        assert score_hand_low(50.0) == 100.0   # 50 % → max
        assert score_hand_low(80.0) == 100.0   # clampé
        assert score_hand_low(None) is None

    def test_exposure_amplified(self):
        assert score_exposure(20.0) == 40.0    # 20 % du bâti en zone basse
        assert score_exposure(50.0) == 100.0
        assert score_exposure(None) is None

    def test_history_events(self):
        assert score_history(0) == 0.0
        assert score_history(1) == 25.0
        assert score_history(4) == 100.0       # 4 événements = max
        assert score_history(10) == 100.0      # clampé
        assert score_history(None) is None

    def test_elevation(self):
        assert score_elevation(0.0) == 100.0
        assert score_elevation(2.0) == 100.0
        assert score_elevation(60.0) == 0.0
        assert score_elevation(None) is None

    def test_flat_direct(self):
        assert score_flat(35.0) == 35.0
        assert score_flat(120.0) == 100.0

    def test_impervious_and_water(self):
        assert score_impervious(42.0) == 42.0
        assert score_water(25.0) == 100.0
        assert score_water(10.0) == 40.0


class TestLevels:
    def test_thresholds(self):
        assert level_for(0) == "faible"
        assert level_for(24.9) == "faible"
        assert level_for(25) == "modere"
        assert level_for(50) == "eleve"
        assert level_for(75) == "critique"


class TestCompute:
    FULL = {
        "hand_low_pct": 50.0,       # → 100
        "built_low_pct": 50.0,      # → 100
        "history_events": 4,        # → 100
        "elevation_mean_m": 2.0,    # → 100
        "urban_pct": 100.0,         # → 100
        "water_pct": 25.0,          # → 100
        "flat_pct": 100.0,          # → 100
    }

    def test_all_max_gives_100(self):
        result = compute(self.FULL)
        assert result["susceptibility"] == 100.0
        assert result["level"] == "critique"

    def test_all_min_gives_0(self):
        result = compute({
            "hand_low_pct": 0.0, "built_low_pct": 0.0, "history_events": 0,
            "elevation_mean_m": 80.0, "urban_pct": 0.0, "water_pct": 0.0,
            "flat_pct": 0.0,
        })
        assert result["susceptibility"] == 0.0
        assert result["level"] == "faible"

    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_missing_history_renormalizes(self):
        """Sans couche d'événements, l'indice reste comparable 0-100."""
        partial = dict(self.FULL, history_events=None)
        result = compute(partial)
        assert result["susceptibility"] == 100.0
        assert result["scores"]["history"] is None

    def test_history_raises_score(self):
        """Des inondations observées doivent remonter la susceptibilité —
        le cas Cocody : physiographie moyenne mais événements récents."""
        base = {
            "hand_low_pct": 15.0, "built_low_pct": 12.0,
            "elevation_mean_m": 56.0, "urban_pct": 65.0,
            "water_pct": 5.5, "flat_pct": 25.0,
        }
        without = compute(dict(base, history_events=None))["susceptibility"]
        with_events = compute(dict(base, history_events=4))["susceptibility"]
        assert with_events > without + 10   # remontée nette

    def test_no_factor_returns_none(self):
        assert compute({}) is None
