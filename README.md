# Golf Coach Agent

An AI-powered golf coaching system that automatically downloads your Rapsodo MLM2PRO session data from R-Cloud, analyzes your swing metrics and video frames with a Vision LLM, and delivers a focused coaching report — one flaw, one drill, one target metric.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Orchestrator                            │
│              agents/orchestrator.py                         │
└───────────┬─────────────────────────────┬───────────────────┘
            │                             │
            ▼                             ▼
┌───────────────────────┐    ┌────────────────────────────────┐
│     Scout Tool        │    │        Coach Agent             │
│  tools/rapsodo_tool   │    │    agents/coach_agent.py       │
└───────────┬───────────┘    └──────────────┬─────────────────┘
            │                               │
    ┌───────┴────────┐              ┌───────┴──────────┐
    │                │              │                  │
    ▼                ▼              ▼                  ▼
┌────────┐  ┌──────────────┐  ┌─────────┐   ┌────────────────┐
│Scraper │  │Preprocessor  │  │ Vision  │   │History Tracker │
│(R-Cloud│  │(Stats+Frames)│  │  LLM    │   │  (SQLite)      │
│ login) │  │              │  │Analysis │   │                │
└────────┘  └──────────────┘  └─────────┘   └────────────────┘
```

**Data flow for one coaching session:**
1. Playwright logs into R-Cloud, intercepts JSON API responses, downloads videos
2. Preprocessor computes per-club stats and extracts 4 key frames per shot
3. Vision LLM (Claude or GPT-4o) analyzes address/backswing/impact/follow-through frames
4. Coach Agent combines metrics + vision output into one targeted report
5. History database tracks trends across all sessions

## Prerequisites

- Python 3.11+
- Rapsodo MLM2PRO with an **active Premium Membership** (required for R-Cloud sync)
- An API key for either **Anthropic** (Claude) or **OpenAI** (GPT-4o)
- `gh` CLI installed and authenticated (`gh auth login`)

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/amcheste/golf-coach-agent.git
cd golf-coach-agent
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure credentials

```bash
cp config/.env.template .env
```

Edit `.env` and fill in:
```
RAPSODO_EMAIL=your@email.com
RAPSODO_PASSWORD=yourpassword
ANTHROPIC_API_KEY=sk-ant-...     # or OPENAI_API_KEY=sk-...
```

### 3. Initial Login (run once)

R-Cloud requires a real browser login. Run this once to save your session:

```bash
python scripts/initial_login.py
```

A Chromium window will open. The script will auto-fill your credentials. If R-Cloud sends an email OTP, check your inbox, enter it in the browser, and press Enter in the terminal once you can see your sessions dashboard.

This saves `config/storage_state.json` — your session token. After this, the system runs fully headless.

> **Security note:** `storage_state.json` is gitignored. Never commit it. Treat it like a password.

If your session expires (typically after 30 days), just re-run `scripts/initial_login.py`.

## Usage

### CLI

```bash
# Analyze yesterday's session
python agents/orchestrator.py --date yesterday

# Analyze a specific date
python agents/orchestrator.py --date 2026-03-25

# Natural language dates
python agents/orchestrator.py --date "last Tuesday"
python agents/orchestrator.py --date "last Friday"

# Debug mode (visible browser window)
python agents/orchestrator.py --date yesterday --debug
```

### Python

```python
from agents.orchestrator import run_coaching_session

# Returns the coaching report as a string
report = run_coaching_session("yesterday")
print(report)

# Or with a specific date
report = run_coaching_session("2026-03-25")
```

### Just the download tool (no coaching report)

```python
from tools.rapsodo_tool import RapsodoCoachTool

tool = RapsodoCoachTool()
package = tool.run("2026-03-25")

print(f"Downloaded {package['shot_count']} shots")
print(f"Extracted {package['frame_count']} key frames")
print(package['session_analysis'])
```

## Output

Each session creates a directory at `rapsodo_vault/YYYY-MM-DD/` containing:

```
rapsodo_vault/
└── 2026-03-25/
    ├── videos/
    │   ├── shot_01_7Iron_152yds_impact.mp4
    │   ├── shot_01_7Iron_152yds_shot.mp4
    │   └── ...
    ├── frames/
    │   ├── shot_01_impact_address.jpg
    │   ├── shot_01_impact_top_of_backswing.jpg
    │   ├── shot_01_impact_impact.jpg
    │   ├── shot_01_impact_follow_through.jpg
    │   └── ...
    ├── shots_raw.json         # Raw intercepted API data
    ├── session_analysis.json  # Per-club stats + outliers
    ├── video_metadata.json    # Shot ↔ video ↔ frame mapping
    ├── raw_data.csv           # CSV export (if available)
    └── coaching_report.md     # The final coaching report
```

The `rapsodo_vault/` directory is gitignored — videos and images stay local.

### Sample Coaching Report

```
## Session Snapshot
- Solid ball-striking session: 42 shots across 4 clubs, avg smash factor 1.41
- Best club: 7-Iron (avg carry 152 yds, lowest dispersion at ±8 yds)
- Biggest concern: Driver path averaging -4.2° (out-to-in) with face +1.8° open

## The Big Miss
- Data Evidence: Driver Club Path -4.2°, Face Angle +1.8°, avg sidespin 2,800 RPM right
- Visual Evidence: At Top of Backswing, club is laid-off and across the line; at Impact,
  right shoulder is high and leading path is steeply left of target

## Root Cause
An over-the-top downswing initiated by the right shoulder, causing the steep out-to-in
path that produces the push-slice pattern visible in both the data and frames.

## The Prescription
- Drill: "Right Pocket Drill" — feel the right hip pocket moving toward the target on
  the downswing before the arms move. Keep the right shoulder passive and low through impact.
- Feel Cue: "Shallow the club by feeling like the right elbow drops into your right hip pocket"
- Target Metric for Next Session: Driver Club Path within -1° to +1°

## Historical Context
Club path has trended from -2.1° (3 sessions ago) to -4.2° today — this is worsening.
The face has stayed relatively stable, meaning the path drift is the primary driver of the miss.
```

## Troubleshooting

**"No session found for date"**
- Make sure you've synced the session from the MLM2PRO app to R-Cloud before running
- Check that your Premium subscription is active at golf-cloud.rapsodo.com
- Run with `--debug` to see the browser and confirm the session appears in the UI

**"No shots were extracted"**
- R-Cloud may have updated its internal API response format
- Run with `--debug` and check the terminal output for `[Intercept] Captured JSON from:` lines
- Open browser DevTools → Network tab to find the actual API endpoints being called

**Session state expired**
```bash
python scripts/initial_login.py
```

**Browser not found**
```bash
playwright install chromium
```

## Historical Trends

Query your history database directly:

```python
from src.history_tracker import get_trend_summary, list_sessions

# See all recorded sessions
print(list_sessions())

# Trend for Driver carry over last 5 sessions
print(get_trend_summary("Driver", "carry_distance_yds", last_n_sessions=5))

# Club path trend for 7-Iron
print(get_trend_summary("7Iron", "club_path_deg", last_n_sessions=8))
```
