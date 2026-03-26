"""
Rapsodo R-Cloud Scraper
-----------------------
Authenticates with golf-cloud.rapsodo.com, intercepts the JSON API responses
the portal makes internally, and downloads shot data + videos for a given session date.
"""

import asyncio
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiofiles
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext, Response

load_dotenv()

BASE_URL = "https://golf-cloud.rapsodo.com"
STATE_FILE = Path(__file__).parent.parent / "config" / "storage_state.json"
VAULT_DIR = Path(__file__).parent.parent / "rapsodo_vault"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _jitter(min_s: float = 1.5, max_s: float = 3.0):
    """Random human-like delay between actions."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _download_file(url: str, dest: Path, context: BrowserContext):
    """Download a file via the authenticated browser context."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Use a new page so we stay authenticated via cookies
    page = await context.new_page()
    try:
        async with page.expect_download() as dl_info:
            await page.goto(url)
        download = await dl_info.value
        await download.save_as(dest)
    except Exception:
        # Fallback: fetch via API request (works for direct file URLs)
        api_req = await context.request.get(url)
        if api_req.ok:
            body = await api_req.body()
            async with aiofiles.open(dest, "wb") as f:
                await f.write(body)
        else:
            raise RuntimeError(f"Failed to download {url} — status {api_req.status}")
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def _perform_login(page: Page, context: BrowserContext, headed: bool):
    """Navigate to login page and authenticate. Saves session state afterwards."""
    email = os.getenv("RAPSODO_EMAIL", "")
    password = os.getenv("RAPSODO_PASSWORD", "")

    if not email or not password:
        raise ValueError(
            "RAPSODO_EMAIL and RAPSODO_PASSWORD must be set in your .env file."
        )

    print("[Auth] Navigating to login page...")
    await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    await _jitter(1, 2)

    # Fill email
    await page.fill('input[type="email"], input[name="email"], input[placeholder*="mail" i]', email)
    await _jitter(0.5, 1)

    # Fill password
    await page.fill('input[type="password"]', password)
    await _jitter(0.5, 1)

    # Submit
    await page.click('button[type="submit"]')

    if headed:
        print("[Auth] Browser is visible. Complete any MFA/OTP prompts, then press Enter here...")
        input()
    else:
        # Wait for redirect away from login page
        await page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)

    print("[Auth] Login successful. Saving session state...")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(STATE_FILE))
    print(f"[Auth] Session saved to {STATE_FILE}")


async def _get_authenticated_context(playwright, debug: bool = False):
    """Return a browser + context, using saved session state if available."""
    headed = debug or os.getenv("PLAYWRIGHT_DEBUG", "false").lower() == "true"
    browser = await playwright.chromium.launch(headless=not headed)

    if STATE_FILE.exists():
        print("[Auth] Loading saved session state...")
        context = await browser.new_context(storage_state=str(STATE_FILE))
    else:
        print("[Auth] No saved session found — starting manual login flow...")
        context = await browser.new_context()
        page = await context.new_page()
        await _perform_login(page, context, headed=True)
        await page.close()

    return browser, context


# ---------------------------------------------------------------------------
# Session Discovery
# ---------------------------------------------------------------------------

async def _find_session_for_date(page: Page, target_date: str) -> bool:
    """
    Navigate the Sessions list and click into the session matching target_date.
    Returns True if found, False otherwise.
    target_date format: YYYY-MM-DD
    """
    print(f"[Scout] Looking for session on {target_date}...")

    await page.goto(f"{BASE_URL}/sessions", wait_until="networkidle")
    await _jitter(2, 3)

    # R-Cloud renders sessions as cards — look for the date in text content
    # Try multiple selector strategies since the UI may vary
    session_cards = page.locator('[class*="session"], [class*="Session"], [data-testid*="session"]')
    count = await session_cards.count()
    print(f"[Scout] Found {count} session cards on page...")

    # Search for the target date in any visible text (handles MM/DD/YYYY and YYYY-MM-DD formats)
    # Convert YYYY-MM-DD to multiple display formats for matching
    from datetime import datetime
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    date_variants = [
        target_date,                          # 2026-03-25
        dt.strftime("%-m/%-d/%Y"),            # 3/25/2026
        dt.strftime("%m/%d/%Y"),              # 03/25/2026
        dt.strftime("%B %-d, %Y"),            # March 25, 2026
        dt.strftime("%b %-d, %Y"),            # Mar 25, 2026
    ]

    for i in range(count):
        card = session_cards.nth(i)
        card_text = await card.inner_text()
        if any(variant in card_text for variant in date_variants):
            print(f"[Scout] Found matching session. Clicking in...")
            await card.click()
            await page.wait_for_load_state("networkidle")
            await _jitter(2, 3)
            return True

    # If not found on first page, try scrolling or pagination
    print(f"[Scout] Session not visible — attempting scroll/pagination search...")
    for _ in range(5):
        await page.keyboard.press("End")
        await _jitter(1, 2)
        count_new = await session_cards.count()
        if count_new == count:
            break  # No new cards loaded
        count = count_new
        for i in range(count):
            card = session_cards.nth(i)
            card_text = await card.inner_text()
            if any(variant in card_text for variant in date_variants):
                print(f"[Scout] Found matching session after scroll. Clicking in...")
                await card.click()
                await page.wait_for_load_state("networkidle")
                await _jitter(2, 3)
                return True

    print(f"[Scout] No session found for {target_date}.")
    return False


# ---------------------------------------------------------------------------
# Network Interception — the key to getting clean JSON data
# ---------------------------------------------------------------------------

def _build_intercept_handler(captured: list):
    """
    Returns an async handler that captures JSON responses from Rapsodo's
    internal API calls as the session page loads.
    """
    async def handler(response: Response):
        url = response.url
        # Target JSON API responses (avoid images, CSS, JS bundles)
        if (
            response.status == 200
            and "json" in response.headers.get("content-type", "")
            and any(kw in url for kw in [
                "/api/", "/sessions/", "/shots/", "/telemetry/",
                "rapsodo", "golf-cloud"
            ])
        ):
            try:
                body = await response.json()
                captured.append({"url": url, "data": body})
                print(f"[Intercept] Captured JSON from: {url}")
            except Exception:
                pass  # Binary or malformed — skip
    return handler


def _extract_shots_from_captured(captured: list) -> list[dict]:
    """
    Parse the captured JSON responses to find shot-level data.
    Returns a list of shot dicts with all available metrics.
    """
    shots = []
    for item in captured:
        data = item["data"]

        # Handle various response shapes Rapsodo might use
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            # Common keys: shots, data, results, items, session_shots
            for key in ["shots", "data", "results", "items", "session_shots", "measurements"]:
                if key in data and isinstance(data[key], list):
                    candidates = data[key]
                    break
            else:
                candidates = [data]
        else:
            continue

        for item in candidates:
            if not isinstance(item, dict):
                continue
            # Heuristic: a shot dict should have at least ball speed or launch angle
            has_metrics = any(k in item for k in [
                "ballSpeed", "ball_speed", "launchAngle", "launch_angle",
                "totalSpin", "total_spin", "carryDistance", "carry_distance"
            ])
            if has_metrics:
                shots.append(item)

    return shots


def _normalize_shot(raw: dict, index: int) -> dict:
    """Normalize varied field names into a consistent schema."""
    def get(*keys):
        for k in keys:
            if k in raw:
                return raw[k]
        return None

    return {
        "shot_number": get("shotNumber", "shot_number", "index") or index + 1,
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


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

async def fetch_session(target_date: str, debug: bool = False) -> dict:
    """
    Full pipeline for one session date:
    1. Authenticate (or load saved session)
    2. Find the session matching target_date
    3. Intercept JSON to extract shot metrics
    4. Download all videos

    Returns a result dict with session_path, shots, and download summary.
    """
    session_dir = VAULT_DIR / target_date
    session_dir.mkdir(parents=True, exist_ok=True)
    video_dir = session_dir / "videos"
    video_dir.mkdir(exist_ok=True)

    captured_responses = []

    async with async_playwright() as p:
        browser, context = await _get_authenticated_context(p, debug=debug)
        page = await context.new_page()

        # Attach network interceptor before navigating
        page.on("response", _build_intercept_handler(captured_responses))

        # Find and enter the session
        found = await _find_session_for_date(page, target_date)
        if not found:
            await browser.close()
            return {
                "success": False,
                "error": f"No session found for {target_date}",
                "session_path": str(session_dir),
            }

        # Give the page extra time to fire all XHR/fetch calls
        await _jitter(3, 5)
        await page.wait_for_load_state("networkidle")
        await _jitter(2, 3)

        # Try to trigger a CSV export if the button exists
        try:
            export_btn = page.locator('button:has-text("Export"), a:has-text("CSV"), [aria-label*="export" i]')
            if await export_btn.count() > 0:
                print("[Scout] Attempting CSV export...")
                async with page.expect_download(timeout=10_000) as dl_info:
                    await export_btn.first.click()
                dl = await dl_info.value
                await dl.save_as(session_dir / "raw_data.csv")
                print("[Scout] CSV downloaded.")
        except Exception as e:
            print(f"[Scout] CSV export not available or failed: {e}")

        # Extract shots from intercepted JSON
        raw_shots = _extract_shots_from_captured(captured_responses)
        shots = [_normalize_shot(s, i) for i, s in enumerate(raw_shots)]
        print(f"[Scout] Extracted {len(shots)} shots from intercepted responses.")

        # Download videos
        downloaded_videos = []
        for shot in shots:
            shot_num = str(shot["shot_number"]).zfill(2)
            club = re.sub(r"[^a-zA-Z0-9]", "", str(shot["club"] or "Unknown"))
            carry = int(shot["carry_distance_yds"] or 0)

            for video_type, url_key in [("impact", "impact_video_url"), ("shot", "shot_video_url")]:
                url = shot.get(url_key)
                if not url:
                    continue
                filename = f"shot_{shot_num}_{club}_{carry}yds_{video_type}.mp4"
                dest = video_dir / filename
                if dest.exists():
                    print(f"[Scout] Skipping (already downloaded): {filename}")
                else:
                    try:
                        print(f"[Scout] Downloading {filename}...")
                        await _download_file(url, dest, context)
                        downloaded_videos.append(str(dest))
                        await _jitter(1, 2)
                    except Exception as e:
                        print(f"[Scout] Failed to download {filename}: {e}")

        # Save raw shots JSON
        shots_for_json = [{k: v for k, v in s.items() if k != "_raw"} for s in shots]
        raw_path = session_dir / "shots_raw.json"
        async with aiofiles.open(raw_path, "w") as f:
            await f.write(json.dumps(shots_for_json, indent=2, default=str))

        await browser.close()

    return {
        "success": True,
        "session_path": str(session_dir),
        "shot_count": len(shots),
        "shots": shots,
        "downloaded_videos": downloaded_videos,
        "captured_api_responses": len(captured_responses),
    }
