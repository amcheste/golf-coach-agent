"""
Tests for the direct API fast path in src/rapsodo_scraper.py.

_try_direct_fetch talks to R-Cloud through the authenticated browser context,
so these tests substitute a fake context that serves canned responses — no
network, no real browser. Playwright must be installed (it is in
requirements.txt) but no browser binaries are needed.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import rcloud_api
from rapsodo_scraper import _try_direct_fetch

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

SESSION_LIST_URL = "https://golf-cloud.rapsodo.com/api/v1/sessions?limit=50"
SHOT_URL_TEMPLATE = "https://golf-cloud.rapsodo.com/api/v1/sessions/{session_id}/shots"

MANIFEST = {
    "session_list_url": SESSION_LIST_URL,
    "shot_url_templates": [SHOT_URL_TEMPLATE],
    "learned_from_date": "2026-03-22",
}

SESSION_LIST_PAYLOAD = {
    "sessions": [
        {"id": 987654, "startTime": "2026-03-25T14:02:11Z"},
        {"id": 981234, "startTime": "2026-03-22T09:30:00Z"},
    ]
}

SHOTS_PAYLOAD = {
    "shots": [
        {"shotNumber": 1, "ballSpeed": 118.4, "club": "7Iron"},
        {"shotNumber": 2, "ballSpeed": 120.1, "club": "7Iron"},
    ]
}


class FakeResponse:
    def __init__(self, payload=None, status=200, content_type="application/json"):
        self._payload = payload
        self.status = status
        self.ok = 200 <= status < 300
        self.headers = {"content-type": content_type}

    async def json(self):
        return self._payload


class FakeRequest:
    """Stands in for context.request — serves canned responses by URL."""

    def __init__(self, responses: dict):
        self._responses = responses
        self.requested_urls: list[str] = []

    async def get(self, url: str):
        self.requested_urls.append(url)
        result = self._responses.get(url, FakeResponse(status=404))
        if isinstance(result, Exception):
            raise result
        return result


class FakeContext:
    def __init__(self, responses: dict):
        self.request = FakeRequest(responses)


def run_direct_fetch(context, target_date="2026-03-25"):
    return asyncio.run(_try_direct_fetch(context, target_date))


def use_manifest(tmp_path, monkeypatch, manifest=MANIFEST):
    monkeypatch.setattr(rcloud_api, "MANIFEST_PATH", tmp_path / "api_manifest.json")
    if manifest is not None:
        rcloud_api.save_manifest(manifest)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTryDirectFetch:
    def test_happy_path_returns_shot_responses(self, tmp_path, monkeypatch):
        use_manifest(tmp_path, monkeypatch)
        context = FakeContext(
            {
                SESSION_LIST_URL: FakeResponse(SESSION_LIST_PAYLOAD),
                SHOT_URL_TEMPLATE.replace("{session_id}", "987654"): FakeResponse(SHOTS_PAYLOAD),
            }
        )
        captured = run_direct_fetch(context)
        assert len(captured) == 1
        shots = rcloud_api.extract_shots_from_captured(captured)
        assert len(shots) == 2
        # The session id for the requested date was substituted into the template
        assert context.request.requested_urls[-1].endswith("/sessions/987654/shots")

    def test_no_manifest_makes_no_requests(self, tmp_path, monkeypatch):
        use_manifest(tmp_path, monkeypatch, manifest=None)
        context = FakeContext({})
        assert run_direct_fetch(context) == []
        assert context.request.requested_urls == []

    def test_expired_auth_falls_back(self, tmp_path, monkeypatch):
        # A 401 (or login redirect) on the session list must return [] so the
        # caller falls back to UI interception.
        use_manifest(tmp_path, monkeypatch)
        context = FakeContext({SESSION_LIST_URL: FakeResponse(status=401)})
        assert run_direct_fetch(context) == []

    def test_html_response_falls_back(self, tmp_path, monkeypatch):
        # Expired sessions often 200 with an HTML login page instead of JSON
        use_manifest(tmp_path, monkeypatch)
        context = FakeContext(
            {SESSION_LIST_URL: FakeResponse(status=200, content_type="text/html")}
        )
        assert run_direct_fetch(context) == []

    def test_date_not_in_session_list_falls_back(self, tmp_path, monkeypatch):
        use_manifest(tmp_path, monkeypatch)
        context = FakeContext({SESSION_LIST_URL: FakeResponse(SESSION_LIST_PAYLOAD)})
        assert run_direct_fetch(context, target_date="2026-01-01") == []

    def test_network_error_falls_back(self, tmp_path, monkeypatch):
        use_manifest(tmp_path, monkeypatch)
        context = FakeContext({SESSION_LIST_URL: ConnectionError("boom")})
        assert run_direct_fetch(context) == []

    def test_failed_shot_fetch_yields_empty_capture(self, tmp_path, monkeypatch):
        # Session list resolves, but the shot endpoint 404s — captured list is
        # empty, so the caller falls back.
        use_manifest(tmp_path, monkeypatch)
        context = FakeContext({SESSION_LIST_URL: FakeResponse(SESSION_LIST_PAYLOAD)})
        assert run_direct_fetch(context) == []

    def test_shot_fetch_exception_is_contained(self, tmp_path, monkeypatch):
        use_manifest(tmp_path, monkeypatch)
        context = FakeContext(
            {
                SESSION_LIST_URL: FakeResponse(SESSION_LIST_PAYLOAD),
                SHOT_URL_TEMPLATE.replace("{session_id}", "987654"): ConnectionError("boom"),
            }
        )
        assert run_direct_fetch(context) == []
