"""
Tests for statistical helpers and per-club analysis in src/preprocessor.py
No network, no file I/O, no credentials needed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from preprocessor import (
    _detect_outliers,
    _overall_summary,
    _per_club_stats,
    _safe_mean,
    _safe_std,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_shot(num, club, carry, smash=None, ball_speed=None, path=None, face=None):
    return {
        "shot_number": num,
        "club": club,
        "carry_distance_yds": carry,
        "smash_factor": smash,
        "ball_speed_mph": ball_speed,
        "club_path_deg": path,
        "face_angle_deg": face,
        "club_speed_mph": None,
        "launch_angle_deg": None,
        "backspin_rpm": None,
        "sidespin_rpm": None,
        "spin_axis_deg": None,
        "total_distance_yds": None,
        "angle_of_attack_deg": None,
        "lateral_yds": None,
    }


SEVEN_IRON_SHOTS = [
    make_shot(1, "7Iron", 150, smash=1.42, ball_speed=118),
    make_shot(2, "7Iron", 155, smash=1.44, ball_speed=120),
    make_shot(3, "7Iron", 145, smash=1.38, ball_speed=115),
    make_shot(4, "7Iron", 152, smash=1.41, ball_speed=117),
    make_shot(5, "7Iron", 148, smash=1.40, ball_speed=116),
]

DRIVER_SHOTS = [
    make_shot(6, "Driver", 230, smash=1.48, ball_speed=155),
    make_shot(7, "Driver", 245, smash=1.50, ball_speed=158),
    make_shot(8, "Driver", 210, smash=1.35, ball_speed=142),
]

ALL_SHOTS = SEVEN_IRON_SHOTS + DRIVER_SHOTS


# ---------------------------------------------------------------------------
# _safe_mean
# ---------------------------------------------------------------------------


class TestSafeMean:
    def test_normal_values(self):
        assert _safe_mean([1.0, 2.0, 3.0]) == 2.0

    def test_with_none_values(self):
        assert _safe_mean([1.0, None, 3.0]) == 2.0

    def test_all_none(self):
        assert _safe_mean([None, None]) is None

    def test_empty(self):
        assert _safe_mean([]) is None

    def test_single_value(self):
        assert _safe_mean([42.5]) == 42.5

    def test_rounds_to_two_decimal(self):
        result = _safe_mean([1.0, 2.0, 3.0, 4.0])
        assert result == 2.5


# ---------------------------------------------------------------------------
# _safe_std
# ---------------------------------------------------------------------------


class TestSafeStd:
    def test_known_std(self):
        # sample std (ddof=1) of [2, 4, 4, 4, 5, 5, 7, 9] ≈ 2.14
        result = _safe_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        assert abs(result - 2.14) < 0.01

    def test_single_value_returns_none(self):
        assert _safe_std([5.0]) is None

    def test_empty_returns_none(self):
        assert _safe_std([]) is None

    def test_all_same_is_zero(self):
        assert _safe_std([3.0, 3.0, 3.0]) == 0.0

    def test_filters_none(self):
        result = _safe_std([1.0, None, 3.0, None, 5.0])
        result_clean = _safe_std([1.0, 3.0, 5.0])
        assert result == result_clean


# ---------------------------------------------------------------------------
# _per_club_stats
# ---------------------------------------------------------------------------


class TestPerClubStats:
    def test_clubs_identified(self):
        stats = _per_club_stats(ALL_SHOTS)
        assert "7Iron" in stats
        assert "Driver" in stats

    def test_shot_counts(self):
        stats = _per_club_stats(ALL_SHOTS)
        assert stats["7Iron"]["shot_count"] == 5
        assert stats["Driver"]["shot_count"] == 3

    def test_carry_averages_are_reasonable(self):
        stats = _per_club_stats(ALL_SHOTS)
        seven_carry = stats["7Iron"]["averages"]["carry_distance_yds"]
        driver_carry = stats["Driver"]["averages"]["carry_distance_yds"]
        assert 140 < seven_carry < 165
        assert 210 < driver_carry < 260

    def test_driver_carry_greater_than_seven_iron(self):
        stats = _per_club_stats(ALL_SHOTS)
        assert (
            stats["Driver"]["averages"]["carry_distance_yds"]
            > stats["7Iron"]["averages"]["carry_distance_yds"]
        )

    def test_std_dev_present(self):
        stats = _per_club_stats(ALL_SHOTS)
        std = stats["7Iron"]["std_devs"]["carry_distance_yds"]
        assert std is not None
        assert std >= 0

    def test_single_shot_std_is_none(self):
        single = [make_shot(1, "PW", 120, smash=1.40)]
        stats = _per_club_stats(single)
        assert stats["PW"]["std_devs"]["carry_distance_yds"] is None

    def test_unknown_club_grouped(self):
        shots = [make_shot(1, None, 100), make_shot(2, None, 105)]
        stats = _per_club_stats(shots)
        assert "Unknown" in stats


# ---------------------------------------------------------------------------
# _detect_outliers
# ---------------------------------------------------------------------------


class TestDetectOutliers:
    def test_best_worst_identified(self):
        outliers = _detect_outliers(ALL_SHOTS)
        assert "7Iron" in outliers
        # Shot 2 (smash 1.44) should be in best
        assert 2 in outliers["7Iron"]["best_3"]
        # Shot 3 (smash 1.38) should be in worst
        assert 3 in outliers["7Iron"]["worst_3"]

    def test_no_smash_factor_skipped(self):
        shots = [make_shot(i, "8Iron", 140) for i in range(5)]  # all smash=None
        outliers = _detect_outliers(shots)
        assert "8Iron" not in outliers

    def test_less_than_three_shots_handled(self):
        shots = [make_shot(1, "SW", 80, smash=1.30), make_shot(2, "SW", 85, smash=1.35)]
        outliers = _detect_outliers(shots)
        assert len(outliers["SW"]["best_3"]) <= 2
        assert len(outliers["SW"]["worst_3"]) <= 2


# ---------------------------------------------------------------------------
# _overall_summary
# ---------------------------------------------------------------------------


class TestOverallSummary:
    def test_total_shots(self):
        per_club = _per_club_stats(ALL_SHOTS)
        summary = _overall_summary(ALL_SHOTS, per_club)
        assert summary["total_shots"] == 8

    def test_clubs_used(self):
        per_club = _per_club_stats(ALL_SHOTS)
        summary = _overall_summary(ALL_SHOTS, per_club)
        assert set(summary["clubs_used"]) == {"7Iron", "Driver"}

    def test_most_consistent_club_is_identified(self):
        # 7Iron shots are closer together in carry; Driver has a 35-yd outlier (shot 8 at 210)
        per_club = _per_club_stats(ALL_SHOTS)
        summary = _overall_summary(ALL_SHOTS, per_club)
        assert summary["most_consistent_club"] == "7Iron"

    def test_best_smash_club_is_driver(self):
        per_club = _per_club_stats(ALL_SHOTS)
        summary = _overall_summary(ALL_SHOTS, per_club)
        assert summary["best_smash_factor_club"] == "Driver"
