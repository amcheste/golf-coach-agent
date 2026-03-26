"""
History Tracker
---------------
Maintains a SQLite database of per-club session averages so the Coach Agent
can identify trends across multiple sessions (e.g., carry distance improving,
spin rate drifting, path getting worse over 3 weeks).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

VAULT_DIR = Path(__file__).parent.parent / "rapsodo_vault"
DB_PATH = VAULT_DIR / "master_history.db"

TRACKED_METRICS = [
    "ball_speed_mph",
    "club_speed_mph",
    "launch_angle_deg",
    "backspin_rpm",
    "sidespin_rpm",
    "smash_factor",
    "carry_distance_yds",
    "total_distance_yds",
    "club_path_deg",
    "face_angle_deg",
    "angle_of_attack_deg",
]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    TEXT NOT NULL,
    club            TEXT NOT NULL,
    shot_count      INTEGER,
    ball_speed_mph          REAL,
    club_speed_mph          REAL,
    launch_angle_deg        REAL,
    backspin_rpm            REAL,
    sidespin_rpm            REAL,
    smash_factor            REAL,
    carry_distance_yds      REAL,
    total_distance_yds      REAL,
    club_path_deg           REAL,
    face_angle_deg          REAL,
    angle_of_attack_deg     REAL,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(session_date, club)
);

CREATE TABLE IF NOT EXISTS raw_shots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    TEXT NOT NULL,
    shot_number     INTEGER,
    club            TEXT,
    ball_speed_mph          REAL,
    club_speed_mph          REAL,
    launch_angle_deg        REAL,
    backspin_rpm            REAL,
    sidespin_rpm            REAL,
    spin_axis_deg           REAL,
    smash_factor            REAL,
    carry_distance_yds      REAL,
    total_distance_yds      REAL,
    club_path_deg           REAL,
    face_angle_deg          REAL,
    angle_of_attack_deg     REAL,
    lateral_yds             REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


def _get_conn() -> sqlite3.Connection:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_session(session_date: str, session_analysis: dict, shots: list[dict]):
    """
    Insert or replace per-club averages for a session date.
    Also stores all raw shots for granular trend queries.
    """
    conn = _get_conn()
    try:
        per_club = session_analysis.get("per_club_stats", {})

        for club, stats in per_club.items():
            averages = stats.get("averages", {})
            conn.execute(
                """
                INSERT INTO session_summary
                    (session_date, club, shot_count,
                     ball_speed_mph, club_speed_mph, launch_angle_deg,
                     backspin_rpm, sidespin_rpm, smash_factor,
                     carry_distance_yds, total_distance_yds,
                     club_path_deg, face_angle_deg, angle_of_attack_deg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_date, club) DO UPDATE SET
                    shot_count = excluded.shot_count,
                    ball_speed_mph = excluded.ball_speed_mph,
                    club_speed_mph = excluded.club_speed_mph,
                    launch_angle_deg = excluded.launch_angle_deg,
                    backspin_rpm = excluded.backspin_rpm,
                    sidespin_rpm = excluded.sidespin_rpm,
                    smash_factor = excluded.smash_factor,
                    carry_distance_yds = excluded.carry_distance_yds,
                    total_distance_yds = excluded.total_distance_yds,
                    club_path_deg = excluded.club_path_deg,
                    face_angle_deg = excluded.face_angle_deg,
                    angle_of_attack_deg = excluded.angle_of_attack_deg
                """,
                (
                    session_date, club, stats.get("shot_count"),
                    averages.get("ball_speed_mph"), averages.get("club_speed_mph"),
                    averages.get("launch_angle_deg"), averages.get("backspin_rpm"),
                    averages.get("sidespin_rpm"), averages.get("smash_factor"),
                    averages.get("carry_distance_yds"), averages.get("total_distance_yds"),
                    averages.get("club_path_deg"), averages.get("face_angle_deg"),
                    averages.get("angle_of_attack_deg"),
                ),
            )

        for shot in shots:
            conn.execute(
                """
                INSERT INTO raw_shots
                    (session_date, shot_number, club,
                     ball_speed_mph, club_speed_mph, launch_angle_deg,
                     backspin_rpm, sidespin_rpm, spin_axis_deg, smash_factor,
                     carry_distance_yds, total_distance_yds,
                     club_path_deg, face_angle_deg, angle_of_attack_deg, lateral_yds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_date,
                    shot.get("shot_number"), shot.get("club"),
                    shot.get("ball_speed_mph"), shot.get("club_speed_mph"),
                    shot.get("launch_angle_deg"), shot.get("backspin_rpm"),
                    shot.get("sidespin_rpm"), shot.get("spin_axis_deg"),
                    shot.get("smash_factor"), shot.get("carry_distance_yds"),
                    shot.get("total_distance_yds"), shot.get("club_path_deg"),
                    shot.get("face_angle_deg"), shot.get("angle_of_attack_deg"),
                    shot.get("lateral_yds"),
                ),
            )

        conn.commit()
        print(f"[History] Saved {len(per_club)} club summaries + {len(shots)} shots for {session_date}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read / Trend queries
# ---------------------------------------------------------------------------

def get_trend(club: str, metric: str, last_n_sessions: int = 5) -> list[dict]:
    """
    Return the per-session average of `metric` for `club` over the last N sessions.
    Ordered oldest → newest so the coach can see direction of change.

    Args:
        club: Club name (e.g. "7Iron", "Driver")
        metric: Column name (e.g. "carry_distance_yds", "club_path_deg")
        last_n_sessions: How many sessions to look back

    Returns:
        List of dicts: [{"session_date": ..., "value": ...}, ...]
    """
    if metric not in TRACKED_METRICS:
        raise ValueError(f"Unknown metric '{metric}'. Valid: {TRACKED_METRICS}")

    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT session_date, {metric} as value
            FROM session_summary
            WHERE club = ? AND {metric} IS NOT NULL
            ORDER BY session_date DESC
            LIMIT ?
            """,
            (club, last_n_sessions),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]  # oldest first
    finally:
        conn.close()


def get_trend_summary(club: str, metric: str, last_n_sessions: int = 5) -> dict:
    """
    Returns a human-readable trend summary for use by the Coach Agent.
    Includes direction (improving/declining/stable), magnitude, and raw data.
    """
    trend = get_trend(club, metric, last_n_sessions)
    if len(trend) < 2:
        return {
            "club": club,
            "metric": metric,
            "sessions_available": len(trend),
            "direction": "insufficient_data",
            "summary": f"Only {len(trend)} session(s) available for {club} {metric}.",
            "data": trend,
        }

    values = [r["value"] for r in trend if r["value"] is not None]
    first, last = values[0], values[-1]
    delta = last - first
    pct_change = round((delta / first) * 100, 1) if first != 0 else 0

    if abs(pct_change) < 3:
        direction = "stable"
    elif delta > 0:
        direction = "improving" if metric in [
            "carry_distance_yds", "total_distance_yds", "ball_speed_mph",
            "club_speed_mph", "smash_factor"
        ] else "worsening"
    else:
        direction = "worsening" if metric in [
            "carry_distance_yds", "total_distance_yds", "ball_speed_mph",
            "club_speed_mph", "smash_factor"
        ] else "improving"

    summary = (
        f"{club} {metric}: {direction} over {len(trend)} sessions "
        f"({first:.1f} → {last:.1f}, {'+' if delta >= 0 else ''}{delta:.1f} / {pct_change:+.1f}%)"
    )

    return {
        "club": club,
        "metric": metric,
        "sessions_available": len(trend),
        "direction": direction,
        "delta": round(delta, 2),
        "pct_change": pct_change,
        "first_value": first,
        "last_value": last,
        "summary": summary,
        "data": trend,
    }


def get_all_trends_for_session(session_analysis: dict, last_n_sessions: int = 5) -> list[dict]:
    """
    Convenience: generate trend summaries for all clubs and key metrics
    so the Coach Agent gets a complete picture before writing its report.
    """
    priority_metrics = [
        "carry_distance_yds", "smash_factor", "club_path_deg",
        "face_angle_deg", "backspin_rpm",
    ]
    clubs = list(session_analysis.get("per_club_stats", {}).keys())
    results = []
    for club in clubs:
        for metric in priority_metrics:
            trend = get_trend_summary(club, metric, last_n_sessions)
            if trend["sessions_available"] >= 2:
                results.append(trend)
    return results


def list_sessions() -> list[str]:
    """Return all session dates in the history database, newest first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT session_date FROM session_summary ORDER BY session_date DESC"
        ).fetchall()
        return [r["session_date"] for r in rows]
    finally:
        conn.close()
