"""
Tests du scoring de susceptibilité inondation — logique pure, sans DB ni GEE.
"""
from flood.scoring import (
    WEIGHTS,
    compute,
    level_for,
    score_elevation,
    score_hand,
    score_impervious,
    score_slope,
    score_water,
)


class TestSubscores:
    def test_hand_extremes(self):
        assert score_hand(0.0) == 100.0     # au niveau du drainage → max
        assert score_hand(15.0) == 0.0      # 15 m au-dessus → nul
        assert score_hand(30.0) == 0.0      # clampé
        assert score_hand(None) is None

    def test_hand_midpoint(self):
        assert score_hand(7.5) == 50.0

    def test_elevation(self):
        assert score_elevation(0.0) == 100.0
        assert score_elevation(2.0) == 100.0   # littoral
        assert score_elevation(60.0) == 0.0
        assert score_elevation(31.0) == 50.0

    def test_slope_flat_is_worst(self):
        assert score_slope(0.0) == 100.0
        assert score_slope(10.0) == 0.0
        assert score_slope(5.0) == 50.0

    def test_impervious_passthrough_clamped(self):
        assert score_impervious(42.0) == 42.0
        assert score_impervious(150.0) == 100.0
        assert score_impervious(None) is None

    def test_water_amplified(self):
        assert score_water(10.0) == 40.0
        assert score_water(25.0) == 100.0   # 25 % d'eau = max
        assert score_water(50.0) == 100.0   # clampé


class TestLevels:
    def test_thresholds(self):
        assert level_for(0) == "faible"
        assert level_for(24.9) == "faible"
        assert level_for(25) == "modere"
        assert level_for(50) == "eleve"
        assert level_for(75) == "critique"
        assert level_for(100) == "critique"


class TestCompute:
    FULL = {
        "hand_mean_m": 0.0,       # → 100
        "elevation_mean_m": 2.0,  # → 100
        "slope_mean_deg": 0.0,    # → 100
        "urban_pct": 100.0,       # → 100
        "water_pct": 25.0,        # → 100
    }

    def test_all_max_gives_100(self):
        result = compute(self.FULL)
        assert result["susceptibility"] == 100.0
        assert result["level"] == "critique"

    def test_all_min_gives_0(self):
        result = compute({
            "hand_mean_m": 20.0, "elevation_mean_m": 80.0,
            "slope_mean_deg": 15.0, "urban_pct": 0.0, "water_pct": 0.0,
        })
        assert result["susceptibility"] == 0.0
        assert result["level"] == "faible"

    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_missing_factor_renormalizes(self):
        """Sans occupation du sol (GEE partiel), l'indice reste sur 0-100."""
        partial = dict(self.FULL, urban_pct=None, water_pct=None)
        result = compute(partial)
        assert result["susceptibility"] == 100.0
        assert result["scores"]["impervious"] is None

    def test_no_factor_returns_none(self):
        assert compute({}) is None
        assert compute({"hand_mean_m": None}) is None

    def test_monotonic_in_hand(self):
        """Plus on est haut au-dessus du drainage, plus l'indice baisse."""
        low = compute(dict(self.FULL, hand_mean_m=1.0))["susceptibility"]
        high = compute(dict(self.FULL, hand_mean_m=12.0))["susceptibility"]
        assert high < low
