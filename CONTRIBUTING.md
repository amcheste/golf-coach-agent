# Contributing to Golf Coach Agent

Thanks for your interest in contributing. This project is a personal AI coaching tool built around the Rapsodo MLM2PRO, and contributions are welcome — whether that's bug fixes, new club mappings, better frame extraction, or improved coaching prompts.

## Ways to Contribute

- **Bug reports** — open a GitHub Issue with steps to reproduce and the relevant terminal output
- **Scraper fixes** — R-Cloud's internal API changes occasionally; PRs that update field mappings or selector logic are high value
- **Better coaching prompts** — if you have golf instruction expertise, improvements to the Coach Agent persona and output format are welcome
- **New metrics / analysis** — the preprocessor and history tracker are designed to be extended
- **Tests** — more unit tests for edge cases in date parsing, stats, frame extraction

## Development Setup

See [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) for the full local setup walkthrough.

Quick start:
```bash
git clone https://github.com/amcheste/golf-coach-agent.git
cd golf-coach-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
```

## Branching, Commits, and Releases

The branching strategy, commit convention, and release process follow the canonical rules documented in my engineering handbook:

- **Why:** [Branching Strategy philosophy](https://github.com/amcheste/engineering-handbook/blob/main/docs/philosophies/branching-strategy.md)
- **How:** [Branching & Releases workflow](https://github.com/amcheste/engineering-handbook/blob/main/docs/workflows/branching-and-releases.md)

In short: branch from `develop`, one logical change per PR, [Conventional Commits](https://www.conventionalcommits.org/) (`feat:` / `fix:` / `docs:` / `chore:` / `refactor:`, `!` for breaking), and releases are cut by `/publish-release` with a CLI merge from `develop` to `main` (never GitHub's merge button).

## Pull Request Checklist

Before pushing, run the checks locally:
```bash
ruff check .
mypy src/ tools/ agents/
pytest tests/ -v
```
CI will run these automatically, but catching them locally saves a round trip.

Write or update tests for any logic changes in `src/`. The scraper and preprocessor have pure-logic functions (stats, date parsing, frame position math) that are fully testable without R-Cloud credentials.

## What Requires R-Cloud Credentials

The scraper (`src/rapsodo_scraper.py`) cannot be integration-tested in CI because it requires a live R-Cloud session. If you're changing scraper logic:
- Test manually with `--debug` mode so you can see the browser
- Include notes in your PR describing what you observed in DevTools → Network tab

Everything else — stats, frame extraction, date parsing, history queries, the Coach Agent prompt — can be tested without credentials.

## Code Style

- **Formatter:** `ruff format` (line length 100)
- **Linter:** `ruff check` — no warnings expected on merge
- **Type hints:** encouraged in `src/` and `tools/`; `mypy` runs in CI with `--ignore-missing-imports`
- **Comments:** only where logic isn't self-evident; avoid restating what the code does

## Reporting Scraper Breakage

R-Cloud occasionally updates its frontend. If the scraper stops finding sessions or returning shots, open an issue with:
- The date you ran it
- The `[Intercept] Captured JSON from:` lines from terminal output (or lack thereof)
- A note on whether `--debug` mode shows the session loading correctly visually

This helps narrow down whether it's a selector issue, an API response shape change, or an auth problem.
