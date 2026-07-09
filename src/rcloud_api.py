"""
R-Cloud API helpers
-------------------
Pure parsing and endpoint-manifest logic that lets the scraper talk to
R-Cloud's internal API directly instead of driving the web UI.

The first run for any date still uses browser interception (rapsodo_scraper).
Afterwards build_manifest() generalizes the URLs that actually produced shot
data into rapsodo_vault/api_manifest.json:

    {
      "session_list_url": "https://.../api/v1/sessions",
      "shot_url_templates": ["https://.../api/v1/sessions/{session_id}/shots"],
      "learned_from_date": "2026-03-25"
    }

Later runs GET the session list, map date -> session id, substitute the id
into the templates, and skip UI navigation entirely — no CSS selectors, no
scroll-pagination, no human-like jitter. If anything doesn't line up (expired
auth, changed schema, a date not in the list) the scraper falls back to UI
interception and re-learns the manifest.

This module is deliberately playwright-free so all of it is unit-testable.
"""

import json
import re
from pathlib import Path
from typing import Optional

VAULT_DIR = Path(__file__).parent.parent / "rapsodo_vault"
MANIFEST_PATH = VAULT_DIR / "api_manifest.json"

# Keys that may hold a session identifier in a session-list payload
SESSION_ID_KEYS = ["sessionId", "session_id", "sessionUuid", "id", "uuid", "guid"]

# Keys that may wrap a list of items in an API response
LIST_WRAPPER_KEYS = [
    "shots",
    "data",
    "results",
    "items",
    "session_shots",
    "measurements",
    "sessions",
]

# Fields that identify a dict as shot-level data
SHOT_METRIC_KEYS = [
    "ballSpeed",
    "ball_speed",
    "launchAngle",
    "launch_angle",
    "totalSpin",
    "total_spin",
    "carryDistance",
    "carry_distance",
]

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def _candidate_items(data) -> list:
    """Unwrap the list of items from the various response shapes R-Cloud uses."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in LIST_WRAPPER_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def extract_shots_from_captured(captured: list) -> list[dict]:
    """
    Parse captured JSON responses ([{"url": ..., "data": ...}]) to find
    shot-level data. Returns a list of shot dicts with all available metrics,
    deduplicated — the same shot often appears in more than one response
    (e.g. session list + session detail).
    """
    shots = []
    seen: set[str] = set()
    for item in captured:
        for candidate in _candidate_items(item["data"]):
            if not isinstance(candidate, dict):
                continue
            # Heuristic: a shot dict should have at least ball speed or launch angle
            if any(k in candidate for k in SHOT_METRIC_KEYS):
                key = json.dumps(candidate, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    shots.append(candidate)
    return shots


def normalize_shot(raw: dict, index: int) -> dict:
    """Normalize varied field names into a consistent schema."""

    def get(*keys):
        for k in keys:
            if k in raw:
                return raw[k]
        return None

    shot_number = get("shotNumber", "shot_number", "index")
    return {
        "shot_number": shot_number if shot_number is not None else index + 1,
        "club": get("club", "clubType", "club_type", "clubName") or "Unknown",
        "ball_speed_mph": get("ballSpeed", "ball_speed"),
        "club_speed_mph": get("clubSpeed", "club_speed", "clubHeadSpeed"),
        "launch_angle_deg": get("launchAngle", "launch_angle", "launchAngleDeg"),
        "backspin_rpm": get("backSpin", "backspin", "backSpinRate", "totalSpin", "total_spin"),
        "sidespin_rpm": get("sideSpin", "sidespin", "sideSpinRate"),
        "spin_axis_deg": get("spinAxis", "spin_axis"),
        "smash_factor": get("smashFactor", "smash_factor"),
        "carry_distance_yds": get("carryDistance", "carry_distance", "carry"),
        "total_distance_yds": get("totalDistance", "total_distance", "total"),
        "club_path_deg": get("clubPath", "club_path"),
        "face_angle_deg": get("faceAngle", "face_angle", "faceToPath"),
        "angle_of_attack_deg": get("angleOfAttack", "angle_of_attack", "aoa"),
        "lateral_yds": get("lateral", "offline", "dispersion"),
        "impact_video_url": get("impactVideoUrl", "impact_video_url", "impactVideo", "videoUrl"),
        "shot_video_url": get("shotVideoUrl", "shot_video_url", "shotVideo", "downTheLineVideoUrl"),
        "_raw": raw,  # Preserve original for debugging
    }


def _extract_iso_date(text: str) -> Optional[str]:
    """Pull an ISO (YYYY-MM-DD) date out of a string, converting M/D/YYYY if needed."""
    match = _ISO_DATE_RE.search(text)
    if match:
        return match.group(0)
    match = _US_DATE_RE.search(text)
    if match:
        month, day, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def session_date_map(data) -> dict[str, str]:
    """
    Map ISO session date -> session id from an API payload that lists sessions.
    Returns {} if the payload doesn't look like a session list. When multiple
    sessions share a date, the first one wins.
    """
    mapping: dict[str, str] = {}
    for item in _candidate_items(data):
        if not isinstance(item, dict):
            continue
        session_id = next((item[k] for k in SESSION_ID_KEYS if item.get(k) is not None), None)
        if session_id is None:
            continue
        for value in item.values():
            if isinstance(value, str):
                date = _extract_iso_date(value)
                if date:
                    mapping.setdefault(date, str(session_id))
                    break
    return mapping


# ---------------------------------------------------------------------------
# Endpoint manifest
# ---------------------------------------------------------------------------


def build_manifest(captured: list[dict], target_date: str) -> Optional[dict]:
    """
    Generalize a UI-interception capture into a reusable endpoint manifest.
    Returns None when the capture can't be generalized (no session-list payload
    found, or the session id doesn't appear in any shot-bearing URL).
    """
    shot_urls = [item["url"] for item in captured if extract_shots_from_captured([item])]
    shot_url_set = set(shot_urls)

    # Best session-list candidate: a non-shot payload mapping the most dates,
    # which must include the date we just scraped.
    best_url = None
    best_map: dict[str, str] = {}
    for item in captured:
        if item["url"] in shot_url_set:
            continue
        mapping = session_date_map(item["data"])
        if target_date in mapping and len(mapping) > len(best_map):
            best_url, best_map = item["url"], mapping

    if not best_url or not shot_urls:
        return None

    session_id = best_map[target_date]
    # Only template ids long enough that substring replacement is unambiguous.
    if len(session_id) < 4:
        return None
    templates = sorted(
        {url.replace(session_id, "{session_id}") for url in shot_urls if session_id in url}
    )
    if not templates:
        return None

    return {
        "session_list_url": best_url,
        "shot_url_templates": templates,
        "learned_from_date": target_date,
    }


def load_manifest() -> Optional[dict]:
    """Load the endpoint manifest, or None if missing/corrupt/incomplete."""
    try:
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if not manifest.get("session_list_url") or not manifest.get("shot_url_templates"):
        return None
    return manifest


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


def shot_urls_for_session(manifest: dict, session_id: str) -> list[str]:
    """Substitute a session id into the manifest's shot URL templates."""
    return [
        template.replace("{session_id}", str(session_id))
        for template in manifest.get("shot_url_templates", [])
    ]
