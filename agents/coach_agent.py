"""
Golf Coach Agent
----------------
An elite AI teaching professional that analyzes Rapsodo session data + video frames
and produces a single focused coaching prescription for a 17-handicap golfer.

Uses a Vision-capable LLM (Claude or GPT-4o) to examine extracted swing frames
alongside the launch monitor metrics to find the root cause of misses.
"""

import base64
import json
import os
from pathlib import Path
from typing import Optional

from crewai import Agent, Crew, Process, Task
from dotenv import load_dotenv

load_dotenv()

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from rapsodo_tool import download_rapsodo_session

# ---------------------------------------------------------------------------
# Vision Analysis Helper
# ---------------------------------------------------------------------------


def _encode_image_b64(path: str) -> Optional[str]:
    """Read an image file and return base64 encoded string."""
    try:
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def analyze_swing_frames_with_vision(video_metadata: list[dict], session_analysis: dict) -> str:
    """
    Send extracted key frames + metrics to a Vision LLM for biomechanical analysis.
    Prioritizes outlier shots (worst smash factor) for the most instructive analysis.

    Returns: Vision analysis string to feed to the Coach Agent.
    """
    # Determine which provider to use
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not anthropic_key and not openai_key:
        return "[Vision Analysis] No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env"

    # Find the most instructive shots (worst smash factor = biggest mechanical flaw)
    shots_with_frames = [s for s in video_metadata if s.get("frames")]
    if not shots_with_frames:
        return "[Vision Analysis] No extracted frames found. Run preprocessor first."

    # Pick up to 3 shots: worst, median, best smash factor
    scored = sorted(
        [s for s in shots_with_frames if s.get("metrics", {}).get("smash_factor")],
        key=lambda s: s["metrics"]["smash_factor"],
    )
    candidates = []
    if scored:
        candidates.append(scored[0])  # worst
        if len(scored) > 1:
            candidates.append(scored[-1])  # best
        if len(scored) > 2:
            candidates.append(scored[len(scored) // 2])  # median

    # Build prompt context
    per_club_summary = {}
    for club, stats in session_analysis.get("per_club_stats", {}).items():
        avgs = stats.get("averages", {})
        per_club_summary[club] = {
            "shots": stats.get("shot_count"),
            "avg_carry_yds": avgs.get("carry_distance_yds"),
            "avg_smash": avgs.get("smash_factor"),
            "avg_path_deg": avgs.get("club_path_deg"),
            "avg_face_deg": avgs.get("face_angle_deg"),
            "avg_spin_rpm": avgs.get("backspin_rpm"),
        }

    system_prompt = """You are an elite PGA-certified Teaching Professional and Biomechanics Expert.
You are analyzing swing frames and launch monitor data for a 17-handicap golfer.
Your goal is to identify the ONE primary mechanical flaw most responsible for inconsistency.
Be specific about what you see in the images — reference body position, club position, posture.
Keep your analysis focused and actionable. A 17-handicap needs clarity, not complexity."""

    user_prompt = f"""Analyze these golf swing frames and launch monitor data.

SESSION METRICS SUMMARY:
{json.dumps(per_club_summary, indent=2)}

I'm sending you frames from {len(candidates)} shots (worst, median, and best by Smash Factor).
For each shot I'll show: Address, Top of Backswing, Impact, Follow-through.

Please provide:
1. What you observe in the frames (specific body/club positions at each key position)
2. How the visual evidence connects to the metric patterns (e.g., face angle, path numbers)
3. The single most impactful mechanical flaw to fix

Shots being analyzed:
{
        json.dumps(
            [
                {
                    "shot": s["shot_number"],
                    "club": s["club"],
                    "carry_yds": s.get("carry_distance_yds"),
                    "smash_factor": s.get("metrics", {}).get("smash_factor"),
                    "club_path": s.get("metrics", {}).get("club_path_deg"),
                    "face_angle": s.get("metrics", {}).get("face_angle_deg"),
                    "backspin": s.get("metrics", {}).get("backspin_rpm"),
                }
                for s in candidates
            ],
            indent=2,
        )
    }"""

    if anthropic_key:
        return _vision_with_anthropic(system_prompt, user_prompt, candidates, anthropic_key)
    else:
        return _vision_with_openai(system_prompt, user_prompt, candidates, openai_key)


def _vision_with_anthropic(system_prompt: str, user_prompt: str, shots: list, api_key: str) -> str:
    """Call Claude claude-sonnet-4-6 with vision for swing analysis."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    content = [{"type": "text", "text": user_prompt}]

    for shot in shots:
        frames = shot.get("frames", {})
        for position in ["address", "top_of_backswing", "impact", "follow_through"]:
            frame_path = frames.get(position)
            if not frame_path:
                continue
            b64 = _encode_image_b64(frame_path)
            if b64:
                content.append(
                    {
                        "type": "text",
                        "text": f"Shot {shot['shot_number']} — {position.replace('_', ' ').title()}:",
                    }
                )
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    }
                )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],  # type: ignore[dict-item,typeddict-item]
    )
    first_block = response.content[0]
    return first_block.text if hasattr(first_block, "text") else ""


def _vision_with_openai(system_prompt: str, user_prompt: str, shots: list, api_key: str) -> str:
    """Call GPT-4o with vision for swing analysis."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    messages = [
        {"role": "system", "content": system_prompt},
    ]
    content = [{"type": "text", "text": user_prompt}]

    for shot in shots:
        frames = shot.get("frames", {})
        for position in ["address", "top_of_backswing", "impact", "follow_through"]:
            frame_path = frames.get(position)
            if not frame_path:
                continue
            b64 = _encode_image_b64(frame_path)
            if b64:
                content.append(
                    {
                        "type": "text",
                        "text": f"Shot {shot['shot_number']} — {position.replace('_', ' ').title()}:",
                    }
                )
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
                    }
                )

    messages.append({"role": "user", "content": content})  # type: ignore[dict-item]
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,  # type: ignore[list-item]
        max_tokens=1500,
    )
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# CrewAI Agent + Task Definitions
# ---------------------------------------------------------------------------


def build_scout_agent() -> Agent:
    """The Scout: responsible for downloading and packaging session data."""
    return Agent(
        role="Golf Data Scout",
        goal=(
            "Download the requested Rapsodo MLM2PRO session from R-Cloud, "
            "extract all shot metrics and videos, and deliver a structured Session Package."
        ),
        backstory=(
            "You are a meticulous data engineer specializing in sports analytics. "
            "You interface with Rapsodo's R-Cloud portal to extract raw launch monitor data "
            "and video files, then organize them into a clean package for the coaching team."
        ),
        tools=[download_rapsodo_session],
        verbose=True,
        allow_delegation=False,
    )


def build_coach_agent() -> Agent:
    """The Head Coach: analyzes data + vision output and writes the prescription."""
    return Agent(
        role="Elite Golf Teaching Professional",
        goal=(
            "Analyze the Session Package data and swing frame analysis to identify "
            "the single most impactful mechanical flaw and deliver one clear, actionable prescription."
        ),
        backstory=(
            "You are an elite PGA-certified Teaching Professional and Biomechanics Expert "
            "with 20 years of experience coaching golfers from beginners to Tour professionals. "
            "You specialize in helping 10-20 handicap golfers break through plateaus by identifying "
            "the root cause — not the symptoms — of their inconsistency. "
            "You know that overwhelming a student with 10 things to fix guarantees they improve none of them. "
            "Your coaching philosophy: find the ONE thing, fix it completely, then move on."
        ),
        verbose=True,
        allow_delegation=False,
    )


def build_scout_task(scout_agent: Agent, session_date: str) -> Task:
    return Task(
        description=(
            f"Download the Rapsodo R-Cloud session for '{session_date}'. "
            "Use the rapsodo_session_downloader tool and return the full session package JSON. "
            "Include shot count, per-club stats, outlier shots, and confirm frames were extracted."
        ),
        expected_output=(
            "A JSON string containing: session_date, shot_count, session_analysis "
            "(per-club averages + outliers), trend_report, and frames_available list."
        ),
        agent=scout_agent,
    )


def build_coach_task(coach_agent: Agent, vision_analysis: str) -> Task:
    return Task(
        description=f"""
You have received the Session Package from the Scout, and a Vision Analysis of the swing frames.

VISION ANALYSIS:
{vision_analysis}

Using the Scout's data AND the Vision Analysis above, write a complete coaching report
following this exact format:

## Session Snapshot
- [Overall performance in 1 sentence]
- [Best performing club: name + why]
- [Biggest area of concern: metric + what it means]

## The Big Miss
- **Data Evidence:** [Specific metric numbers — path, face, spin, smash — that reveal the pattern]
- **Visual Evidence:** [What you observed in the swing frames and at which position]

## Root Cause
[One clear sentence naming the single mechanical flaw causing the Big Miss]

## The Prescription
- **Drill:** [Name of drill + 2-sentence description of how to do it]
- **Feel Cue:** [What it should feel like internally when done correctly]
- **Target Metric for Next Session:** [One specific number to chase, e.g. "Club Path within ±2°"]

## Historical Context
[1-2 sentences referencing the trend data — is this getting better or worse over recent sessions?]
""",
        expected_output=(
            "A structured coaching report with Session Snapshot, Big Miss, Root Cause, "
            "Prescription, and Historical Context sections."
        ),
        agent=coach_agent,
    )
