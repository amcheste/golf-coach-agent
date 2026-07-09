"""
Shared utilities with no heavy dependencies — safe to import in tests.
"""

import re
from datetime import datetime, timedelta

from dateutil import parser as dateutil_parser


def sanitize_club(club) -> str:
    """
    Normalize a club name for use in filenames, e.g. "7-Iron" -> "7Iron".
    Both the scraper (writing video files) and the preprocessor (finding them)
    must use this so filenames always match.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", str(club or "Unknown"))
    return cleaned or "Unknown"


def resolve_date(date_input: str) -> str:
    """
    Convert natural language or ISO dates to YYYY-MM-DD.
    Handles: "yesterday", "last Tuesday", "2026-03-25", "March 25", etc.
    """
    date_input = date_input.strip().lower()
    today = datetime.today()

    if date_input == "today":
        return today.strftime("%Y-%m-%d")
    if date_input == "yesterday":
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(weekdays):
        if f"last {day}" in date_input or date_input == day:
            days_back = (today.weekday() - i) % 7 or 7
            return (today - timedelta(days=days_back)).strftime("%Y-%m-%d")

    try:
        parsed = dateutil_parser.parse(date_input, default=today)
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        raise ValueError(
            f"Could not parse date: '{date_input}'. "
            "Use formats like '2026-03-25', 'yesterday', 'last Tuesday', or 'March 25'."
        )
