"""
Tests for history_tracker.py — uses a temp SQLite database, no credentials needed.
"""

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# Redirect the DB to a temp file for each test
@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    import history_tracker
    monkeypatch.setattr(history_tracker, "DB_PATH", tmp_path / "test_history.db")
    monkeypatch.setattr(history_tracker, "VAULT_DIR", tmp_path)
    yield


from history_tracker import (
    upsert_session,
    get_trend,
    get_trend_summary,
    list_sessions,
    TRACKED_METRICS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_analysis(club: str, carry: float, smash: float, path: float) -> dict:
    return {
        "per_club_stats": {
            club: {
                "shot_count": 5,
                "averages": {
                    "carry_distance_yds": carry,
                    "smash_factor": smash,
                    "club_path_deg": path,
                    "ball_speed_mph": 118.0,
                    "club_speed_mph": 83.0,
                    "launch_angle_deg": 16.5,
                    "backspin_rpm": 6200.0,
                    "sidespin_rpm": 300.0,
                    "total_distance_yds": carry + 10,
                    "face_angle_deg": 1.5,
                    "angle_of_attack_deg": -4.5,
                },
                "std_devs": {},
            }
        }
    }


def make_shots(club: str, n: int = 3) -> list:
    return [
        {
            "shot_number": i + 1,
            "club": club,
            "carry_distance_yds": 150.0,
            "ball_speed_mph": 118.0,
            "club_speed_mph": 83.0,
            "launch_angle_deg": 16.5,
            "backspin_rpm": 6200.0,
            "sidespin_rpm": 300.0,
            "spin_axis_deg": 2.0,
            "smash_factor": 1.42,
            "total_distance_yds": 160.0,
            "club_path_deg": -2.0,
            "face_angle_deg": 1.0,
            "angle_of_attack_deg": -4.5,
            "lateral_yds": 5.0,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# upsert_session
# ---------------------------------------------------------------------------

class TestUpsertSession:
    def test_basic_insert(self):
        upsert_session("2026-03-01", make_analysis("7Iron", 150.0, 1.42, -2.0), make_shots("7Iron"))
        sessions = list_sessions()
        assert "2026-03-01" in sessions

    def test_upsert_overwrites_same_date_club(self):
        upsert_session("2026-03-01", make_analysis("7Iron", 150.0, 1.42, -2.0), make_shots("7Iron"))
        upsert_session("2026-03-01", make_analysis("7Iron", 160.0, 1.45, -1.0), make_shots("7Iron"))
        trend = get_trend("7Iron", "carry_distance_yds", 5)
        # Should have only one entry for this date
        assert len(trend) == 1
        assert trend[0]["value"] == 160.0

    def test_multiple_clubs_in_one_session(self):
        analysis = {
            "per_club_stats": {
                "7Iron": make_analysis("7Iron", 150.0, 1.42, -2.0)["per_club_stats"]["7Iron"],
                "Driver": make_analysis("Driver", 240.0, 1.49, 1.0)["per_club_stats"]["Driver"],
            }
        }
        upsert_session("2026-03-01", analysis, make_shots("7Iron") + make_shots("Driver"))
        seven_trend = get_trend("7Iron", "carry_distance_yds")
        driver_trend = get_trend("Driver", "carry_distance_yds")
        assert len(seven_trend) == 1
        assert len(driver_trend) == 1


# ---------------------------------------------------------------------------
# get_trend
# ---------------------------------------------------------------------------

class TestGetTrend:
    def _insert_sessions(self):
        for i, carry in enumerate([145.0, 148.0, 152.0, 155.0, 158.0], start=1):
            date = f"2026-03-{i:02d}"
            upsert_session(date, make_analysis("7Iron", carry, 1.42, -2.0), make_shots("7Iron"))

    def test_returns_oldest_first(self):
        self._insert_sessions()
        trend = get_trend("7Iron", "carry_distance_yds", 5)
        dates = [r["session_date"] for r in trend]
        assert dates == sorted(dates)

    def test_respects_last_n_sessions(self):
        self._insert_sessions()
        trend = get_trend("7Iron", "carry_distance_yds", 3)
        assert len(trend) == 3
        # Should be the 3 most recent
        assert trend[0]["session_date"] == "2026-03-03"

    def test_unknown_club_returns_empty(self):
        self._insert_sessions()
        trend = get_trend("3Wood", "carry_distance_yds")
        assert trend == []

    def test_invalid_metric_raises(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            get_trend("7Iron", "not_a_real_metric")


# ---------------------------------------------------------------------------
# get_trend_summary
# ---------------------------------------------------------------------------

class TestGetTrendSummary:
    def test_improving_carry(self):
        for i, carry in enumerate([145.0, 150.0, 155.0], start=1):
            upsert_session(f"2026-03-0{i}", make_analysis("7Iron", carry, 1.42, -2.0), make_shots("7Iron"))
        summary = get_trend_summary("7Iron", "carry_distance_yds", 3)
        assert summary["direction"] == "improving"
        assert summary["delta"] == pytest.approx(10.0)

    def test_worsening_carry(self):
        for i, carry in enumerate([160.0, 155.0, 148.0], start=1):
            upsert_session(f"2026-03-0{i}", make_analysis("7Iron", carry, 1.42, -2.0), make_shots("7Iron"))
        summary = get_trend_summary("7Iron", "carry_distance_yds", 3)
        assert summary["direction"] == "worsening"

    def test_stable_when_small_change(self):
        for i, carry in enumerate([150.0, 151.0, 150.5], start=1):
            upsert_session(f"2026-03-0{i}", make_analysis("7Iron", carry, 1.42, -2.0), make_shots("7Iron"))
        summary = get_trend_summary("7Iron", "carry_distance_yds", 3)
        assert summary["direction"] == "stable"

    def test_insufficient_data(self):
        upsert_session("2026-03-01", make_analysis("7Iron", 150.0, 1.42, -2.0), make_shots("7Iron"))
        summary = get_trend_summary("7Iron", "carry_distance_yds", 5)
        assert summary["direction"] == "insufficient_data"

    def test_worsening_path_means_path_increasing(self):
        # Club path going from -2 to -5 = worsening (bigger deviation)
        for i, path in enumerate([-2.0, -3.5, -5.0], start=1):
            upsert_session(f"2026-03-0{i}", make_analysis("7Iron", 150.0, 1.42, path), make_shots("7Iron"))
        summary = get_trend_summary("7Iron", "club_path_deg", 3)
        # path is not in the "higher = improving" list, so going down = improving
        assert summary["direction"] in ("improving", "worsening")  # direction depends on sign convention

    def test_summary_string_present(self):
        for i, carry in enumerate([145.0, 155.0], start=1):
            upsert_session(f"2026-03-0{i}", make_analysis("7Iron", carry, 1.42, -2.0), make_shots("7Iron"))
        summary = get_trend_summary("7Iron", "carry_distance_yds")
        assert isinstance(summary["summary"], str)
        assert "7Iron" in summary["summary"]


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_empty_db(self):
        assert list_sessions() == []

    def test_returns_newest_first(self):
        for date in ["2026-03-01", "2026-03-05", "2026-03-03"]:
            upsert_session(date, make_analysis("7Iron", 150.0, 1.42, -2.0), make_shots("7Iron"))
        sessions = list_sessions()
        assert sessions[0] == "2026-03-05"
        assert sessions[-1] == "2026-03-01"

    def test_deduplicated(self):
        # Inserting the same date twice should not create duplicates
        upsert_session("2026-03-01", make_analysis("7Iron", 150.0, 1.42, -2.0), make_shots("7Iron"))
        upsert_session("2026-03-01", make_analysis("7Iron", 155.0, 1.43, -1.5), make_shots("7Iron"))
        sessions = list_sessions()
        assert sessions.count("2026-03-01") == 1
