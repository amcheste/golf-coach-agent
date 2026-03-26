"""
Coach's Assistant Preprocessor
--------------------------------
Takes raw session data from the scraper and produces:
  1. session_analysis.json  — per-club stats, outliers, overall summary
  2. Key frames extracted from each Impact Vision video (4 per shot)
  3. Image-enhanced frames ready for Vision LLM analysis
  4. video_metadata.json    — maps every video/frame to its shot metrics
"""

import json
import math
import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageEnhance

VAULT_DIR = Path(__file__).parent.parent / "rapsodo_vault"

# Frame positions as % of video duration
FRAME_POSITIONS = {
    "address": 0.05,
    "top_of_backswing": 0.40,
    "impact": 0.65,
    "follow_through": 0.85,
}


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------


def _safe_mean(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _safe_std(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    variance = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
    return round(math.sqrt(variance), 2)


def _per_club_stats(shots: list[dict]) -> dict:
    """Group shots by club and compute averages + std devs for key metrics."""
    clubs: dict[str, list] = {}
    for shot in shots:
        club = shot.get("club") or "Unknown"
        clubs.setdefault(club, []).append(shot)

    result = {}
    metrics = [
        "ball_speed_mph",
        "club_speed_mph",
        "launch_angle_deg",
        "backspin_rpm",
        "smash_factor",
        "carry_distance_yds",
        "total_distance_yds",
        "club_path_deg",
        "face_angle_deg",
    ]
    for club, club_shots in clubs.items():
        result[club] = {
            "shot_count": len(club_shots),
            "averages": {m: _safe_mean([s.get(m) for s in club_shots]) for m in metrics},
            "std_devs": {m: _safe_std([s.get(m) for s in club_shots]) for m in metrics},
        }
    return result


def _detect_outliers(shots: list[dict]) -> dict:
    """
    Identify top 3 best and worst shots per club.
    Best = highest Smash Factor. Worst = lowest Smash Factor OR greatest carry deviation.
    """
    clubs: dict[str, list] = {}
    for shot in shots:
        club = shot.get("club") or "Unknown"
        clubs.setdefault(club, []).append(shot)

    outliers = {}
    for club, club_shots in clubs.items():
        with_smash = [s for s in club_shots if s.get("smash_factor") is not None]
        if not with_smash:
            continue
        sorted_shots = sorted(with_smash, key=lambda s: s["smash_factor"], reverse=True)
        outliers[club] = {
            "best_3": [s["shot_number"] for s in sorted_shots[:3]],
            "worst_3": [s["shot_number"] for s in sorted_shots[-3:]],
        }
    return outliers


def _overall_summary(shots: list[dict], per_club: dict) -> dict:
    """High-level session summary."""
    clubs_used = list(per_club.keys())

    # Most consistent club = lowest carry std dev
    most_consistent = None
    lowest_std = float("inf")
    for club, stats in per_club.items():
        std = stats["std_devs"].get("carry_distance_yds")
        if std is not None and std < lowest_std:
            lowest_std = std
            most_consistent = club

    # Best smash factor club
    best_smash_club = None
    best_smash = 0.0
    for club, stats in per_club.items():
        avg_smash = stats["averages"].get("smash_factor") or 0
        if avg_smash > best_smash:
            best_smash = avg_smash
            best_smash_club = club

    return {
        "total_shots": len(shots),
        "clubs_used": clubs_used,
        "most_consistent_club": most_consistent,
        "most_consistent_carry_std_yds": round(lowest_std, 2)
        if lowest_std != float("inf")
        else None,
        "best_smash_factor_club": best_smash_club,
        "best_smash_factor_avg": round(best_smash, 3) if best_smash else None,
    }


# ---------------------------------------------------------------------------
# Key frame extraction
# ---------------------------------------------------------------------------


def _enhance_frame(frame: np.ndarray) -> np.ndarray:
    """
    Improve contrast and brightness for indoor/garage lighting conditions.
    Uses CLAHE (Contrast Limited Adaptive Histogram Equalization) on luminance.
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)
    enhanced_lab = cv2.merge([l_enhanced, a, b])
    enhanced_bgr = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

    # Mild brightness boost via Pillow
    pil_img = Image.fromarray(cv2.cvtColor(enhanced_bgr, cv2.COLOR_BGR2RGB))
    pil_img = ImageEnhance.Brightness(pil_img).enhance(1.15)
    pil_img = ImageEnhance.Contrast(pil_img).enhance(1.10)

    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def extract_key_frames(video_path: Path, output_dir: Path, shot_prefix: str) -> dict[str, str]:
    """
    Extract 4 key frames from a video and save enhanced JPEGs.
    Returns a dict mapping position name -> file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[Preprocessor] Could not open video: {video_path}")
        return {}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    extracted = {}

    # For impact frame, try motion-based detection first
    impact_frame_idx = _detect_impact_frame(cap, total_frames)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # Reset after detection pass

    for position, pct in FRAME_POSITIONS.items():
        if position == "impact" and impact_frame_idx is not None:
            frame_idx = impact_frame_idx
        else:
            frame_idx = int(total_frames * pct)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            print(f"[Preprocessor] Could not read frame {frame_idx} from {video_path.name}")
            continue

        enhanced = _enhance_frame(frame)
        out_path = output_dir / f"{shot_prefix}_{position}.jpg"
        cv2.imwrite(str(out_path), enhanced, [cv2.IMWRITE_JPEG_QUALITY, 92])
        extracted[position] = str(out_path)

    cap.release()
    return extracted


def _detect_impact_frame(cap: cv2.VideoCapture, total_frames: int) -> Optional[int]:
    """
    Detect the impact frame using frame differencing — impact has maximum motion blur
    in a short window around 60-70% of the video.
    Returns the frame index with highest inter-frame difference in that window.
    """
    search_start = int(total_frames * 0.50)
    search_end = int(total_frames * 0.75)

    max_diff = 0.0
    best_frame: Optional[int] = None
    prev_gray = None

    cap.set(cv2.CAP_PROP_POS_FRAMES, search_start)
    for idx in range(search_start, search_end):
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            diff = cv2.absdiff(gray, prev_gray)
            score = float(np.mean(diff))
            if score > max_diff:
                max_diff = score
                best_frame = idx
        prev_gray = gray

    return best_frame


# ---------------------------------------------------------------------------
# Main preprocessing function
# ---------------------------------------------------------------------------


def preprocess_session(session_path: str, shots: list[dict]) -> dict:
    """
    Full preprocessing pipeline for a downloaded session.

    Args:
        session_path: Path to the session directory (e.g. rapsodo_vault/2026-03-25/)
        shots: List of normalized shot dicts from the scraper

    Returns:
        Dict with paths to generated artifacts.
    """
    session_dir = Path(session_path)
    frames_dir = session_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    print(f"[Preprocessor] Processing {len(shots)} shots in {session_dir.name}...")

    # --- Stats ---
    per_club = _per_club_stats(shots)
    outliers = _detect_outliers(shots)
    summary = _overall_summary(shots, per_club)

    analysis = {
        "summary": summary,
        "per_club_stats": per_club,
        "outliers": outliers,
    }

    analysis_path = session_dir / "session_analysis.json"
    with open(analysis_path, "w") as f:
        json.dump(analysis, f, indent=2, default=str)
    print("[Preprocessor] Saved session_analysis.json")

    # --- Key frame extraction ---
    video_dir = session_dir / "videos"
    video_metadata = []

    for shot in shots:
        shot_num = str(shot.get("shot_number", "??")).zfill(2)
        club = shot.get("club") or "Unknown"
        carry = int(shot.get("carry_distance_yds") or 0)
        prefix = f"shot_{shot_num}"

        shot_meta = {
            "shot_number": shot.get("shot_number"),
            "club": club,
            "carry_distance_yds": carry,
            "metrics": {
                k: shot.get(k)
                for k in [
                    "ball_speed_mph",
                    "club_speed_mph",
                    "launch_angle_deg",
                    "backspin_rpm",
                    "sidespin_rpm",
                    "spin_axis_deg",
                    "smash_factor",
                    "club_path_deg",
                    "face_angle_deg",
                    "angle_of_attack_deg",
                    "lateral_yds",
                ]
            },
            "videos": {},
            "frames": {},
        }

        # Find videos for this shot
        for video_type in ["impact", "shot"]:
            pattern = f"shot_{shot_num}_{club.replace(' ', '')}_{carry}yds_{video_type}.mp4"
            video_path = video_dir / pattern
            if video_path.exists():
                shot_meta["videos"][video_type] = str(video_path)

                # Extract key frames from impact video (primary coaching video)
                if video_type == "impact":
                    frames = extract_key_frames(video_path, frames_dir, f"{prefix}_{video_type}")
                    shot_meta["frames"] = frames
                    print(f"[Preprocessor] Extracted {len(frames)} frames for shot {shot_num}")
            else:
                # Try partial match (filename may vary slightly)
                candidates = list(video_dir.glob(f"shot_{shot_num}_*_{video_type}.mp4"))
                if candidates:
                    video_path = candidates[0]
                    shot_meta["videos"][video_type] = str(video_path)
                    if video_type == "impact":
                        frames = extract_key_frames(
                            video_path, frames_dir, f"{prefix}_{video_type}"
                        )
                        shot_meta["frames"] = frames

        video_metadata.append(shot_meta)

    metadata_path = session_dir / "video_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(video_metadata, f, indent=2, default=str)
    print("[Preprocessor] Saved video_metadata.json")

    total_frames = sum(len(s["frames"]) for s in video_metadata)
    print(f"[Preprocessor] Done. {total_frames} key frames extracted across {len(shots)} shots.")

    return {
        "session_analysis_path": str(analysis_path),
        "video_metadata_path": str(metadata_path),
        "frames_dir": str(frames_dir),
        "total_frames_extracted": total_frames,
        "session_analysis": analysis,
        "video_metadata": video_metadata,
    }
