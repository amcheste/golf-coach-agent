"""
Golf Coach Orchestrator
-----------------------
The main entry point for the Rapsodo Golf Coach Agent pipeline.

Usage:
    # CLI
    python agents/orchestrator.py --date "yesterday"
    python agents/orchestrator.py --date "2026-03-25"
    python agents/orchestrator.py --date "last Tuesday"

    # Python import
    from agents.orchestrator import run_coaching_session
    report = run_coaching_session("yesterday")
    print(report)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from crewai import Crew, Process
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from coach_agent import (
    analyze_swing_frames_with_vision,
    build_coach_agent,
    build_coach_task,
    build_scout_agent,
    build_scout_task,
)
from rapsodo_tool import RapsodoCoachTool, _resolve_date

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_coaching_session(session_date: str, debug: bool = False) -> str:
    """
    Full pipeline: Scout downloads → Vision analyzes frames → Coach writes report.

    Args:
        session_date: Any common date format ("yesterday", "2026-03-25", "last Tuesday")
        debug:        Run browser in headed (visible) mode for troubleshooting

    Returns:
        Coaching report as a formatted string.
    """
    resolved_date = _resolve_date(session_date)
    print(f"\n{'=' * 60}")
    print(f"  Golf Coach Agent — Session: {resolved_date}")
    print(f"{'=' * 60}\n")

    # --- Phase 1: Download + Preprocess (direct, faster than going through CrewAI for this) ---
    print("[Orchestrator] Phase 1: Downloading session from R-Cloud...")
    tool = RapsodoCoachTool()
    session_package = tool.run(resolved_date, debug=debug)

    if not session_package.get("success"):
        error = session_package.get("error", "Unknown error")
        print(f"\n[Orchestrator] Pipeline failed: {error}")
        if "debug_hint" in session_package:
            print(f"[Hint] {session_package['debug_hint']}")
        return f"Error: Could not retrieve session for {resolved_date}. {error}"

    print(
        f"\n[Orchestrator] Session loaded: {session_package['shot_count']} shots, "
        f"{session_package['frame_count']} frames extracted."
    )

    # --- Phase 2: Vision Analysis ---
    print("\n[Orchestrator] Phase 2: Running Vision analysis on swing frames...")
    vision_analysis = analyze_swing_frames_with_vision(
        session_package.get("video_metadata", []),
        session_package.get("session_analysis", {}),
    )
    print("[Orchestrator] Vision analysis complete.")

    # --- Phase 3: Coach Agent generates report ---
    print("\n[Orchestrator] Phase 3: Coach Agent writing report...\n")

    coach = build_coach_agent()

    # The Scout task is informational here — we already have the data
    # Pass the session package summary directly to the coach task context
    session_summary_for_coach = json.dumps(
        {
            "session_date": session_package["session_date"],
            "shot_count": session_package["shot_count"],
            "session_analysis": session_package["session_analysis"],
            "trend_report": session_package.get("trend_report", []),
        },
        indent=2,
        default=str,
    )

    coach_task_description = f"""
The Scout has already downloaded and processed the session. Here is the complete data package:

SESSION DATA:
{session_summary_for_coach}

VISION ANALYSIS OF SWING FRAMES:
{vision_analysis}

Using the data AND the vision analysis, write a complete coaching report in this exact format:

## Session Snapshot
- [Overall performance in 1 sentence]
- [Best performing club: name + why]
- [Biggest area of concern: metric + what it means]

## The Big Miss
- **Data Evidence:** [Specific metric numbers that reveal the pattern — path, face, spin, smash]
- **Visual Evidence:** [What was observed in the swing frames and at which position]

## Root Cause
[One clear sentence naming the single mechanical flaw causing the Big Miss]

## The Prescription
- **Drill:** [Name of drill + 2-sentence description of how to do it]
- **Feel Cue:** [What it should feel like internally when done correctly]
- **Target Metric for Next Session:** [One specific number to chase]

## Historical Context
[1-2 sentences referencing the trend data — is this pattern getting better or worse?]
"""

    from crewai import Task

    final_coach_task = Task(
        description=coach_task_description,
        expected_output=(
            "A structured coaching report with Session Snapshot, Big Miss, Root Cause, "
            "Prescription (with drill, feel cue, and target metric), and Historical Context."
        ),
        agent=coach,
    )

    crew = Crew(
        agents=[coach],
        tasks=[final_coach_task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff()
    report = str(result)

    # Save the report
    session_dir = Path(session_package["session_path"])
    report_path = session_dir / "coaching_report.md"
    with open(report_path, "w") as f:
        f.write(f"# Coaching Report — {resolved_date}\n\n")
        f.write(report)
    print(f"\n[Orchestrator] Report saved to: {report_path}")

    print(f"\n{'=' * 60}")
    print(report)
    print(f"{'=' * 60}\n")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Golf Coach Agent — analyze a Rapsodo session by date",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agents/orchestrator.py --date yesterday
  python agents/orchestrator.py --date "last Tuesday"
  python agents/orchestrator.py --date 2026-03-25
  python agents/orchestrator.py --date "March 25" --debug
        """,
    )
    parser.add_argument(
        "--date",
        "-d",
        required=True,
        help='Session date (e.g. "yesterday", "last Tuesday", "2026-03-25")',
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run browser in headed (visible) mode for troubleshooting",
    )
    args = parser.parse_args()

    report = run_coaching_session(args.date, debug=args.debug)
    sys.exit(0 if report and not report.startswith("Error:") else 1)


if __name__ == "__main__":
    main()
