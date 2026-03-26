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
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Add src/ to path so imports work regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils import resolve_date  # lightweight — safe for test imports

# Heavy imports are deferred to function bodies so test collection doesn't
# require playwright / crewai / opencv to be installed.
_crewai_tool = None


def _get_crewai_tool_decorator():
    global _crewai_tool
    if _crewai_tool is None:
        from crewai.tools import tool as crewai_tool
        _crewai_tool = crewai_tool
    return _crewai_tool


# Re-export for backwards compatibility with orchestrator imports
_resolve_date = resolve_date


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
        # Lazy imports so this module is importable without playwright/crewai installed
        from rapsodo_scraper import fetch_session
        from preprocessor import preprocess_session
        from history_tracker import upsert_session, get_all_trends_for_session

        resolved_date = resolve_date(session_date)
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
        from preprocessor import preprocess_session
        from history_tracker import upsert_session, get_all_trends_for_session
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
# The decorator is applied lazily so this module loads without crewai installed.
# ---------------------------------------------------------------------------

_tool_instance = RapsodoCoachTool()


def _make_crewai_tool():
    """Build and return the CrewAI-decorated tool function. Call once at agent startup."""
    from crewai.tools import tool

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
        summary = {
            "success": result.get("success"),
            "session_date": result.get("session_date"),
            "session_path": result.get("session_path"),
            "shot_count": result.get("shot_count"),
            "frame_count": result.get("frame_count"),
            "error": result.get("error"),
            "session_analysis": result.get("session_analysis"),
            "trend_report": result.get("trend_report"),
            "sample_shots": result.get("video_metadata", [])[:5],
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

    return download_rapsodo_session


# Eagerly build the tool when crewai is available (i.e., at agent runtime).
# Falls back gracefully if crewai is not installed (e.g., during test collection).
try:
    download_rapsodo_session = _make_crewai_tool()
except Exception:
    def download_rapsodo_session(session_date: str) -> str:  # type: ignore[misc]
        """Fallback when crewai is not installed."""
        return json.dumps(_tool_instance.run(session_date), default=str)
