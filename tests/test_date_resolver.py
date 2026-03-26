"""
Tests for natural language date resolution in src/utils.py
No network calls, no credentials needed.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils import resolve_date as _resolve_date

TODAY = datetime.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
YESTERDAY_STR = (TODAY - timedelta(days=1)).strftime("%Y-%m-%d")


class TestResolveDateExact:
    def test_iso_format(self):
        assert _resolve_date("2026-03-25") == "2026-03-25"

    def test_iso_format_with_whitespace(self):
        assert _resolve_date("  2026-03-25  ") == "2026-03-25"

    def test_today(self):
        assert _resolve_date("today") == TODAY_STR

    def test_yesterday(self):
        assert _resolve_date("yesterday") == YESTERDAY_STR


class TestResolveDateRelativeWeekday:
    """'last <weekday>' should return the most recent past occurrence of that day."""

    def test_last_monday(self):
        result = _resolve_date("last monday")
        dt = datetime.strptime(result, "%Y-%m-%d")
        assert dt.weekday() == 0  # Monday
        assert dt < TODAY
        assert (TODAY - dt).days <= 7

    def test_last_friday(self):
        result = _resolve_date("last friday")
        dt = datetime.strptime(result, "%Y-%m-%d")
        assert dt.weekday() == 4  # Friday
        assert dt < TODAY

    def test_last_sunday(self):
        result = _resolve_date("last sunday")
        dt = datetime.strptime(result, "%Y-%m-%d")
        assert dt.weekday() == 6  # Sunday
        assert dt < TODAY

    def test_case_insensitive(self):
        lower = _resolve_date("last tuesday")
        upper = _resolve_date("LAST TUESDAY")
        assert lower == upper


class TestResolveDateNatural:
    def test_month_day_year(self):
        assert _resolve_date("March 25 2026") == "2026-03-25"

    def test_abbreviated_month(self):
        assert _resolve_date("Mar 25 2026") == "2026-03-25"

    def test_slash_format(self):
        assert _resolve_date("03/25/2026") == "2026-03-25"


class TestResolveDateInvalid:
    def test_gibberish_raises(self):
        with pytest.raises(ValueError, match="Could not parse date"):
            _resolve_date("not a date at all xyz")

    def test_empty_raises(self):
        with pytest.raises((ValueError, Exception)):
            _resolve_date("")
