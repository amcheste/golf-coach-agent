"""
Initial Login Utility
---------------------
Run this ONCE in headed (visible) mode to authenticate with R-Cloud and save
your session cookies. After this, the scraper will reuse the saved state and
won't need to log in again until the session expires.

Usage:
    python scripts/initial_login.py

What it does:
    1. Opens a visible Chromium browser window
    2. Navigates to golf-cloud.rapsodo.com/login
    3. Auto-fills your credentials from .env
    4. Waits for you to complete any MFA / OTP challenge
    5. Saves session cookies to config/storage_state.json

After running this script, you can run the full agent pipeline headlessly.
"""

import asyncio
import os
import sys
from pathlib import Path

# Allow running from repo root or scripts/ dir
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

STATE_FILE = Path(__file__).parent.parent / "config" / "storage_state.json"
BASE_URL = "https://golf-cloud.rapsodo.com"


async def main():
    email = os.getenv("RAPSODO_EMAIL", "")
    password = os.getenv("RAPSODO_PASSWORD", "")

    if not email or not password:
        print(
            "\n[Error] RAPSODO_EMAIL and RAPSODO_PASSWORD are not set.\n"
            "Copy config/.env.template to .env and fill in your credentials.\n"
        )
        sys.exit(1)

    print("\n=== Rapsodo R-Cloud Initial Login ===")
    print(f"Logging in as: {email}")
    print("A browser window will open. Complete any MFA/OTP if prompted.")
    print("DO NOT close this terminal — press Enter here once you're logged in.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        await asyncio.sleep(1.5)

        # Auto-fill credentials
        try:
            email_sel = 'input[type="email"], input[name="email"], input[placeholder*="mail" i]'
            await page.wait_for_selector(email_sel, timeout=10_000)
            await page.fill(email_sel, email)
            await asyncio.sleep(0.5)
            await page.fill('input[type="password"]', password)
            await asyncio.sleep(0.5)
            await page.click('button[type="submit"]')
            print("[Auth] Credentials submitted. Waiting for you to complete login...")
        except Exception as e:
            print(f"[Auth] Could not auto-fill — please log in manually in the browser. ({e})")

        # Wait for user to confirm they're logged in
        input("\nPress Enter once you are fully logged in and can see your sessions...\n")

        # Verify we're not still on the login page
        current_url = page.url
        if "/login" in current_url:
            print("[Warning] URL still contains '/login' — are you sure you're fully logged in?")
            input("Press Enter again to save anyway, or Ctrl+C to abort: ")

        # Save session state
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(STATE_FILE))
        print(f"\n[Success] Session saved to: {STATE_FILE}")
        print("You can now run the full agent pipeline without manual login.\n")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
