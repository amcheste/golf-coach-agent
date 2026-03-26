# Developer Guide

This guide covers the internal architecture, module responsibilities, how to extend the system, and how to debug the scraper when R-Cloud changes.

## Table of Contents

- [Project Layout](#project-layout)
- [Module Responsibilities](#module-responsibilities)
- [Data Flow](#data-flow)
- [Local Development Setup](#local-development-setup)
- [Running Tests](#running-tests)
- [Debugging the Scraper](#debugging-the-scraper)
- [Extending the System](#extending-the-system)
- [Understanding the Network Interception](#understanding-the-network-interception)
- [Vision LLM Integration](#vision-llm-integration)
- [Adding New Metrics](#adding-new-metrics)

---

## Project Layout

```
golf-coach-agent/
├── src/
│   ├── rapsodo_scraper.py   # Playwright auth, session discovery, network interception, download
│   ├── preprocessor.py      # Stats engine + OpenCV key frame extractor
│   └── history_tracker.py   # SQLite read/write for trend analysis
├── tools/
│   └── rapsodo_tool.py      # CrewAI @tool wrapper + date resolver
├── agents/
│   ├── coach_agent.py       # Vision analysis + CrewAI Agent/Task definitions
│   └── orchestrator.py      # CLI entry point, pipeline coordinator
├── scripts/
│   └── initial_login.py     # One-time headed browser login to save session state
├── config/
│   └── .env.template        # Credential template (never commit .env)
├── tests/
│   ├── test_date_resolver.py
│   ├── test_stats.py
│   └── test_frame_extraction.py
├── docs/
│   └── DEVELOPER_GUIDE.md   # This file
├── rapsodo_vault/           # gitignored — all downloaded data lives here
│   └── YYYY-MM-DD/
│       ├── videos/
│       ├── frames/
│       ├── shots_raw.json
│       ├── session_analysis.json
│       ├── video_metadata.json
│       └── coaching_report.md
└── .github/
    └── workflows/
        └── ci.yml
```

---

## Module Responsibilities

### `src/rapsodo_scraper.py`

The only module that touches the network. Responsibilities:

- **Auth:** Load `config/storage_state.json` for session reuse; fall back to headed login if missing
- **Session discovery:** Scroll the R-Cloud sessions list and find a card matching the target date
- **Network interception:** Attach a Playwright `response` event listener *before* navigating into the session so no API call is missed. All JSON responses from `rapsodo` or `/api/` domains are captured
- **Normalization:** `_normalize_shot()` maps the many possible field name variants from Rapsodo's API into a consistent schema used by the rest of the system
- **Download:** Streams video files via the authenticated browser context

This module is **async throughout** (`async_playwright`, `aiofiles`). The `fetch_session()` coroutine is the public interface; the tool wrapper calls it via `asyncio.run()`.

### `src/preprocessor.py`

Pure data transformation — no network calls. Responsibilities:

- **Stats:** Per-club averages and standard deviations for all key metrics
- **Outlier detection:** Best/worst 3 shots per club by smash factor
- **Frame extraction:** OpenCV reads each video and seeks to 4 positions (address 5%, top of backswing 40%, impact auto-detected via frame differencing, follow-through 85%)
- **Image enhancement:** CLAHE on the LAB luminance channel + Pillow brightness/contrast boost

The impact frame detection (`_detect_impact_frame`) uses frame differencing in the 50–75% window of the video. It finds the frame with the highest inter-frame pixel difference, which corresponds to maximum club head speed at impact.

### `src/history_tracker.py`

SQLite persistence. Two tables:

- `session_summary` — one row per (date, club) with per-club averages. Unique constraint on `(session_date, club)` so re-running a session date does an upsert
- `raw_shots` — every individual shot for granular queries

`get_trend_summary()` classifies direction as improving/worsening/stable using a 3% threshold relative to the first recorded value. The "improving" direction is metric-dependent — higher carry is good, higher path deviation is bad.

### `tools/rapsodo_tool.py`

Thin orchestration wrapper. Responsibilities:

- Natural language date resolution via `dateutil` with manual shortcuts for "yesterday" / "last <weekday>"
- Calls scraper → preprocessor → history tracker in sequence
- Exposes both a `RapsodoCoachTool` class (for programmatic use) and a CrewAI `@tool` decorated function (for agent use)

### `agents/coach_agent.py`

Two concerns in one file:

1. **Vision analysis** (`analyze_swing_frames_with_vision`) — selects the 3 most instructive shots (worst/median/best smash factor), encodes their frames as base64, and calls either Claude or GPT-4o with a structured coaching prompt
2. **CrewAI definitions** — `build_scout_agent()`, `build_coach_agent()`, and corresponding task builders. The Scout agent uses the `download_rapsodo_session` tool; the Coach agent receives the session package + vision output as context

### `agents/orchestrator.py`

The user-facing entry point. It runs Phases 1–3 explicitly rather than relying on CrewAI agent delegation, because:
- Phase 1 (download) is too slow for agent retry loops
- Phase 2 (vision) needs the downloaded frames before it can run
- Phase 3 (report) benefits from CrewAI's structured task execution

---

## Data Flow

```
User: "python orchestrator.py --date yesterday"
  │
  ▼
orchestrator.py
  │  resolve "yesterday" → "2026-03-24"
  │
  ▼
RapsodoCoachTool.run("2026-03-24")
  │
  ├─► rapsodo_scraper.fetch_session()
  │     ├─ Load storage_state.json
  │     ├─ Navigate to R-Cloud sessions
  │     ├─ Intercept JSON API responses  ◄── shot metrics arrive here
  │     ├─ Download .mp4 videos
  │     └─ Save shots_raw.json
  │
  ├─► preprocessor.preprocess_session()
  │     ├─ Compute per-club stats → session_analysis.json
  │     ├─ Extract 4 key frames per video → frames/
  │     └─ Build video_metadata.json
  │
  └─► history_tracker.upsert_session()
        └─ Write to master_history.db
  │
  ▼
analyze_swing_frames_with_vision()
  │  Select worst/median/best shots
  │  Encode frames as base64
  └─► Claude claude-sonnet-4-6 / GPT-4o → vision_analysis string
  │
  ▼
CrewAI Coach Agent Task
  │  Input: session_analysis + trend_report + vision_analysis
  └─► LLM generates coaching_report.md
```

---

## Local Development Setup

```bash
# 1. Clone
git clone https://github.com/amcheste/golf-coach-agent.git
cd golf-coach-agent

# 2. Virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Dependencies
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium

# 4. Credentials
cp config/.env.template .env
# Edit .env — add RAPSODO_EMAIL, RAPSODO_PASSWORD, and one of ANTHROPIC_API_KEY / OPENAI_API_KEY

# 5. Initial login (saves config/storage_state.json)
python scripts/initial_login.py
```

### `requirements-dev.txt`

```
ruff>=0.4.0
mypy>=1.10.0
pytest>=8.2.0
pytest-asyncio>=0.23.0
types-python-dateutil
```

---

## Running Tests

```bash
# All tests
pytest tests/ -v

# Just stats tests (no credentials needed)
pytest tests/test_stats.py -v

# With coverage
pytest tests/ --cov=src --cov=tools --cov-report=term-missing
```

Tests are structured to avoid any network calls or file I/O. The scraper and frame extractor are not unit tested (they require live credentials / real videos) but the pure-logic components are fully covered.

---

## Debugging the Scraper

When a session can't be found or shots aren't being extracted, use `--debug` mode:

```bash
python agents/orchestrator.py --date yesterday --debug
```

This runs the browser in visible (headed) mode so you can see exactly what's happening.

### Checking what the scraper intercepted

Every intercepted JSON response prints:
```
[Intercept] Captured JSON from: https://golf-cloud.rapsodo.com/api/v1/sessions/...
```

If you see 0 captures, the scraper navigated correctly but Rapsodo changed their API URL pattern. Open DevTools in `--debug` mode (right-click → Inspect → Network tab, filter by `Fetch/XHR`) and look for the requests that return shot data. Update the `any(kw in url for kw in [...])` filter in `_build_intercept_handler()` to match the new paths.

### If shot metrics aren't being extracted from captured responses

The `_extract_shots_from_captured()` function looks for lists of dicts with ball speed / launch angle fields. If Rapsodo wraps the shots differently, check `shots_raw.json` in the session directory after a run — it contains the raw intercepted payloads. Add the new wrapper key to the `for key in [...]` list in `_extract_shots_from_captured()`.

### If `_normalize_shot()` is missing a field

Add new field name variants to the appropriate `get()` call in `_normalize_shot()`. Field names seen in the wild so far are documented in the source as the first argument to each `get()` call.

---

## Extending the System

### Adding a new metric

1. Add it to `_normalize_shot()` in `rapsodo_scraper.py`
2. Add it to `TRACKED_METRICS` in `history_tracker.py`
3. Add the SQLite column to the `raw_shots` table schema in `history_tracker.py`
4. Add it to the `session_summary` table and upsert query if you want per-club trend tracking
5. Reference it in the Coach Agent's analysis prompt in `coach_agent.py` if useful

### Adding a new agent to the pod

The system is designed for CrewAI's `Process.sequential`. To add a specialist (e.g., a "Short Game Specialist" that only analyzes wedge shots):

```python
# agents/coach_agent.py
def build_short_game_agent() -> Agent:
    return Agent(
        role="Short Game Specialist",
        goal="Analyze wedge and pitch shot data for trajectory and spin control",
        ...
    )
```

Then add it to the `Crew` in `orchestrator.py` with its own `Task`.

### Changing the Vision provider

The vision routing in `analyze_swing_frames_with_vision()` checks for `ANTHROPIC_API_KEY` first, then `OPENAI_API_KEY`. To force one or the other, simply only set the key for the provider you want in `.env`.

To add a new provider (e.g., Google Gemini), add a `_vision_with_gemini()` function following the same pattern as `_vision_with_anthropic()` and add it to the routing logic.

### Adjusting frame extraction positions

Frame positions are defined as percentages in `FRAME_POSITIONS` at the top of `preprocessor.py`:

```python
FRAME_POSITIONS = {
    "address": 0.05,
    "top_of_backswing": 0.40,
    "impact": 0.65,          # overridden by motion detection when possible
    "follow_through": 0.85,
}
```

The impact frame uses motion-based detection in `_detect_impact_frame()` as long as the video has enough frames. If the detector consistently picks the wrong frame, adjust the `search_start` and `search_end` percentages in that function.

---

## Understanding the Network Interception

R-Cloud is a modern SPA (Single Page Application). When you navigate to a session, the browser makes background fetch requests to Rapsodo's API to load shot data. These requests happen *after* the page URL changes — which is why simple HTTP scraping won't work.

Playwright's `page.on("response", handler)` fires for every HTTP response the browser receives, including background API calls. By attaching this listener before navigating to the session, we capture all the data that the page uses to render itself.

The responses we care about typically look like:
```json
{
  "shots": [
    {
      "shotNumber": 1,
      "ballSpeed": 118.4,
      "launchAngle": 14.2,
      ...
    }
  ]
}
```

The exact URL path and field names have varied across R-Cloud versions, which is why `_normalize_shot()` handles multiple aliases for each field.

---

## Vision LLM Integration

The vision analysis sends up to 12 images (3 shots × 4 frames each) per request. At ~200KB per JPEG, this is well within Claude's and GPT-4o's context limits.

The shot selection logic in `analyze_swing_frames_with_vision()` deliberately picks worst, best, and median shots by smash factor. This gives the Vision LLM a comparison set — it can note what the best shots look like mechanically and contrast them with the worst, which is far more instructive than analyzing random shots.

**Token cost rough estimate per session:**
- Claude claude-sonnet-4-6 with 12 images + ~800 token prompt: ~4,000–6,000 input tokens
- At current pricing this is well under $0.10 per coaching session

If you want to reduce cost further, change the `candidates` selection in `analyze_swing_frames_with_vision()` to only send the worst 1–2 shots.
