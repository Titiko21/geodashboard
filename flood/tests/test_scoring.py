"""
Tests du scoring de susceptibilité inondation v3 — logique pure, sans DB ni GEE.
Couvre la physiographie ET le plancher imposé par les inondations observées.
"""
from flood.scoring import (
    WEIGHTS,
    combine,
    compute,
    compute_physio,
    level_for,
    observed_floor,
    score_elevation,
    score_exposure,
    score_hand_low,
    score_water,
)


class TestSubscores:
    def test_hand_low_amplified(self):
        assert score_hand_low(0.0) == 0.0
        assert score_hand_low(25.0) == 50.0
        assert score_hand_low(50.0) == 100.0
        assert score_hand_low(None) is None

    def test_exposure_amplified(self):
        assert score_exposure(20.0) == 40.0
        assert score_exposure(50.0) == 100.0

    def test_elevation(self):
        assert score_elevation(2.0) == 100.0
        assert score_elevation(60.0) == 0.0

    def test_water_amplified(self):
        assert score_water(25.0) == 100.0


class TestLevels:
    def test_thresholds(self):
        assert level_for(24.9) == "faible"
        assert level_for(25) == "modere"
        assert level_for(50) == "eleve"
        assert level_for(75) == "critique"


class TestObservedFloor:
    def test_no_events_no_floor(self):
        assert observed_floor(0) == 0.0
        assert observed_floor(None) == 0.0

    def test_tiers(self):
        assert observed_floor(1) == 40.0
        assert observed_floor(2) == 40.0
        assert observed_floor(3) == 58.0
        assert observed_floor(5) == 58.0
        assert observed_floor(6) == 72.0
        assert observed_floor(10) == 85.0
        assert observed_floor(15) == 85.0


class TestCombine:
    def test_floor_raises_low_physio(self):
        # Cas Cocody : terrain modéré (39) mais 15 inondations observées.
        r = combine(39.0, 15)
        assert r["susceptibility"] == 85.0
        assert r["level"] == "critique"
        assert r["raised_by_history"] is True

    def test_floor_never_lowers(self):
        # Commune lagunaire (terrain 92) sans relevé : garde son score.
        r = combine(92.3, 0)
        assert r["susceptibility"] == 92.3
        assert r["raised_by_history"] is False

    def test_physio_wins_when_higher(self):
        # Terrain 91 > plancher de 3 événements (58) → garde 91.
        r = combine(91.0, 3)
        assert r["susceptibility"] == 91.0
        assert r["raised_by_history"] is False


class TestCompute:
    FULL = {
        "hand_low_pct": 50.0, "built_low_pct": 50.0, "elevation_mean_m": 2.0,
        "urban_pct": 100.0, "water_pct": 25.0, "flat_pct": 100.0,
    }

    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_all_max(self):
        r = compute(self.FULL, 0)
        assert r["physio"] == 100.0
        assert r["susceptibility"] == 100.0

    def test_missing_factor_renormalizes(self):
        partial = dict(self.FULL, water_pct=None)
        r = compute_physio(partial)
        assert r["physio"] == 100.0
        assert r["scores"]["water"] is None

    def test_history_raises_final(self):
        base = {
            "hand_low_pct": 15.0, "built_low_pct": 12.0, "elevation_mean_m": 56.0,
            "urban_pct": 65.0, "water_pct": 5.5, "flat_pct": 25.0,
        }
        low = compute(base, 0)
        high = compute(base, 12)
        assert high["susceptibility"] > low["susceptibility"]
        assert high["level"] in ("eleve", "critique")

    def test_no_factor_returns_none(self):
        assert compute({}, 5) is None
