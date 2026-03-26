"""
Rapsodo Coach Tool — CrewAI / LangChain Compatible
----------------------------------------------------
Wraps the scraper + preprocessor + history tracker into a single callable
tool that a CrewAI agent can invoke with just a date string.

The tool:
  1. Resolves natural language dates ("yesterday", "last Tuesday") → YYYY-MM-DD
  2. Downloads the session from R-Cloud
  3. Preprocesses videos and stats
  4. Updates the history database
  5. Returns a structured Session Package for the Coach Agent
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Union

from crewai.tools import tool
from dateutil import parser as dateutil_parser
from dotenv import load_dotenv

load_dotenv()

# Add src/ to path so imports work regardless of working directory
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rapsodo_scraper import fetch_session
from preprocessor import preprocess_session
from history_tracker import upsert_session, get_all_trends_for_session


# ---------------------------------------------------------------------------
# Date resolution
# ---------------------------------------------------------------------------

def _resolve_date(date_input: str) -> str:
    """
    Convert natural language or ISO dates to YYYY-MM-DD.
    Handles: "yesterday", "last Tuesday", "2026-03-25", "March 25", etc.
    """
    date_input = date_input.strip().lower()
    today = datetime.today()

    # Common shortcuts
    if date_input in ("today",):
        return today.strftime("%Y-%m-%d")
    if date_input in ("yesterday",):
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # "last <weekday>"
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(weekdays):
        if f"last {day}" in date_input or date_input == day:
            days_back = (today.weekday() - i) % 7 or 7
            return (today - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # Fallback: dateutil parser
    try:
        parsed = dateutil_parser.parse(date_input, default=today)
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        raise ValueError(
            f"Could not parse date: '{date_input}'. "
            "Use formats like '2026-03-25', 'yesterday', 'last Tuesday', or 'March 25'."
        )


# ---------------------------------------------------------------------------
# Core session download logic (sync wrapper around async scraper)
# ---------------------------------------------------------------------------

class RapsodoCoachTool:
    """
    Downloads a Rapsodo R-Cloud session by date, preprocesses all shot data
    and videos, and returns a structured Session Package for the Coach Agent.
    """

    def run(self, session_date: str, debug: bool = False) -> dict:
        """
        Full pipeline for one session date.

        Args:
            session_date: Date string — accepts YYYY-MM-DD, "yesterday", "last Tuesday", etc.
            debug: If True, runs the browser in headed (visible) mode.

        Returns:
            Session Package dict with:
              - session_path: local directory for all session files
              - session_date: resolved YYYY-MM-DD
              - shot_count: number of shots in session
              - session_analysis: per-club stats and outliers
              - video_metadata: per-shot video paths and extracted frame paths
              - trend_report: historical trend summaries for key metrics
              - frame_count: total key frames extracted
        """
        resolved_date = _resolve_date(session_date)
        print(f"\n[RapsodoCoachTool] Starting pipeline for {resolved_date}...")

        # 1. Download from R-Cloud
        scrape_result = asyncio.run(fetch_session(resolved_date, debug=debug))

        if not scrape_result.get("success"):
            return {
                "success": False,
                "session_date": resolved_date,
                "error": scrape_result.get("error", "Unknown scraper error"),
            }

        shots = scrape_result["shots"]
        session_path = scrape_result["session_path"]

        if not shots:
            return {
                "success": False,
                "session_date": resolved_date,
                "error": "Session found but no shots were extracted. "
                         "The intercepted API responses may use an unexpected format.",
                "session_path": session_path,
                "debug_hint": "Run with debug=True to inspect the browser and check network requests.",
            }

        # 2. Preprocess (stats + key frames)
        prep_result = preprocess_session(session_path, shots)

        # 3. Update history database
        upsert_session(resolved_date, prep_result["session_analysis"], shots)

        # 4. Build trend report for Coach Agent
        trend_report = get_all_trends_for_session(prep_result["session_analysis"])

        return {
            "success": True,
            "session_date": resolved_date,
            "session_path": session_path,
            "shot_count": len(shots),
            "frame_count": prep_result["total_frames_extracted"],
            "session_analysis": prep_result["session_analysis"],
            "video_metadata": prep_result["video_metadata"],
            "trend_report": trend_report,
            "api_responses_captured": scrape_result.get("captured_api_responses", 0),
        }


# ---------------------------------------------------------------------------
# CrewAI @tool decorator — drop-in for agent definition
# ---------------------------------------------------------------------------

_tool_instance = RapsodoCoachTool()


@tool("rapsodo_session_downloader")
def download_rapsodo_session(session_date: str) -> str:
    """
    Downloads a Rapsodo MLM2PRO golf session from R-Cloud by date.
    Extracts shot metrics (ball speed, spin, launch angle, club path, etc.),
    downloads Impact Vision and Shot Vision videos, extracts key swing frames,
    and returns a structured coaching package as a JSON string.

    Input: session_date — date string in any common format:
      - Exact: "2026-03-25"
      - Relative: "yesterday", "last Tuesday", "last Friday"
      - Natural: "March 25", "March 25 2026"

    Output: JSON string containing session stats, per-club averages,
            outlier shots, key frame paths, and historical trend data.
    """
    result = _tool_instance.run(session_date)
    # Return serializable summary (exclude large nested dicts from video_metadata)
    summary = {
        "success": result.get("success"),
        "session_date": result.get("session_date"),
        "session_path": result.get("session_path"),
        "shot_count": result.get("shot_count"),
        "frame_count": result.get("frame_count"),
        "error": result.get("error"),
        "session_analysis": result.get("session_analysis"),
        "trend_report": result.get("trend_report"),
        "sample_shots": result.get("video_metadata", [])[:5],  # First 5 for context
        "frames_available": [
            {
                "shot": s.get("shot_number"),
                "club": s.get("club"),
                "frames": list(s.get("frames", {}).keys()),
            }
            for s in result.get("video_metadata", [])
            if s.get("frames")
        ],
    }
    return json.dumps(summary, indent=2, default=str)
