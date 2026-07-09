"""
Tests for src/rcloud_api.py — pure payload parsing and endpoint-manifest
logic. No network calls, no playwright, no credentials needed.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import rcloud_api
from rcloud_api import (
    build_manifest,
    extract_shots_from_captured,
    load_manifest,
    normalize_shot,
    save_manifest,
    session_date_map,
    shot_urls_for_session,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SESSION_LIST_PAYLOAD = {
    "sessions": [
        {"id": 987654, "startTime": "2026-03-25T14:02:11Z", "name": "Range Session"},
        {"id": 981234, "startTime": "2026-03-22T09:30:00Z", "name": "Garage Net"},
    ]
}

SHOTS_PAYLOAD = {
    "shots": [
        {"shotNumber": 1, "ballSpeed": 118.4, "club": "7Iron", "carryDistance": 152},
        {"shotNumber": 2, "ballSpeed": 120.1, "club": "7Iron", "carryDistance": 155},
    ]
}

CAPTURED = [
    {
        "url": "https://golf-cloud.rapsodo.com/api/v1/sessions?limit=50",
        "data": SESSION_LIST_PAYLOAD,
    },
    {"url": "https://golf-cloud.rapsodo.com/api/v1/sessions/987654/shots", "data": SHOTS_PAYLOAD},
]


# ---------------------------------------------------------------------------
# extract_shots_from_captured
# ---------------------------------------------------------------------------


class TestExtractShots:
    def test_finds_shots_in_wrapped_payload(self):
        shots = extract_shots_from_captured(CAPTURED)
        assert len(shots) == 2
        assert shots[0]["ballSpeed"] == 118.4

    def test_bare_list_payload(self):
        captured = [{"url": "u", "data": [{"ball_speed": 110.0}, {"ball_speed": 112.0}]}]
        assert len(extract_shots_from_captured(captured)) == 2

    def test_ignores_non_shot_payloads(self):
        captured = [{"url": "u", "data": SESSION_LIST_PAYLOAD}]
        assert extract_shots_from_captured(captured) == []

    def test_dedups_across_responses(self):
        # Same shots arriving in two different responses count once
        captured = [
            {"url": "a", "data": SHOTS_PAYLOAD},
            {"url": "b", "data": SHOTS_PAYLOAD},
        ]
        assert len(extract_shots_from_captured(captured)) == 2

    def test_non_dict_items_skipped(self):
        captured = [{"url": "u", "data": ["not-a-shot", 42, {"ballSpeed": 100.0}]}]
        assert len(extract_shots_from_captured(captured)) == 1


# ---------------------------------------------------------------------------
# normalize_shot
# ---------------------------------------------------------------------------


class TestNormalizeShot:
    def test_camel_case_aliases(self):
        shot = normalize_shot(
            {"shotNumber": 3, "ballSpeed": 118.4, "clubType": "Driver", "carryDistance": 240},
            0,
        )
        assert shot["shot_number"] == 3
        assert shot["ball_speed_mph"] == 118.4
        assert shot["club"] == "Driver"
        assert shot["carry_distance_yds"] == 240

    def test_shot_number_zero_preserved(self):
        shot = normalize_shot({"shotNumber": 0, "ballSpeed": 100.0}, 4)
        assert shot["shot_number"] == 0

    def test_missing_shot_number_falls_back_to_index(self):
        shot = normalize_shot({"ballSpeed": 100.0}, 4)
        assert shot["shot_number"] == 5

    def test_missing_club_is_unknown(self):
        shot = normalize_shot({"ballSpeed": 100.0}, 0)
        assert shot["club"] == "Unknown"

    def test_raw_preserved(self):
        raw = {"ballSpeed": 100.0, "weird_field": "x"}
        assert normalize_shot(raw, 0)["_raw"] is raw


# ---------------------------------------------------------------------------
# session_date_map
# ---------------------------------------------------------------------------


class TestSessionDateMap:
    def test_iso_datetime_values(self):
        mapping = session_date_map(SESSION_LIST_PAYLOAD)
        assert mapping == {"2026-03-25": "987654", "2026-03-22": "981234"}

    def test_us_date_format_converted(self):
        data = [{"sessionId": "abc-123-def", "date": "3/25/2026"}]
        assert session_date_map(data) == {"2026-03-25": "abc-123-def"}

    def test_items_without_ids_skipped(self):
        data = [{"date": "2026-03-25"}]
        assert session_date_map(data) == {}

    def test_items_without_dates_skipped(self):
        data = [{"id": 1, "name": "no date here"}]
        assert session_date_map(data) == {}

    def test_first_session_wins_for_duplicate_dates(self):
        data = [
            {"id": 111, "startTime": "2026-03-25T08:00:00Z"},
            {"id": 222, "startTime": "2026-03-25T17:00:00Z"},
        ]
        assert session_date_map(data) == {"2026-03-25": "111"}


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------


class TestBuildManifest:
    def test_happy_path(self):
        manifest = build_manifest(CAPTURED, "2026-03-25")
        assert manifest is not None
        assert manifest["session_list_url"] == CAPTURED[0]["url"]
        assert manifest["shot_url_templates"] == [
            "https://golf-cloud.rapsodo.com/api/v1/sessions/{session_id}/shots"
        ]
        assert manifest["learned_from_date"] == "2026-03-25"

    def test_no_session_list_returns_none(self):
        captured = [{"url": "https://x/api/sessions/987654/shots", "data": SHOTS_PAYLOAD}]
        assert build_manifest(captured, "2026-03-25") is None

    def test_date_not_in_list_returns_none(self):
        assert build_manifest(CAPTURED, "2026-01-01") is None

    def test_session_id_not_in_shot_url_returns_none(self):
        captured = [
            CAPTURED[0],
            {"url": "https://x/api/current-session/shots", "data": SHOTS_PAYLOAD},
        ]
        assert build_manifest(captured, "2026-03-25") is None

    def test_short_session_id_not_templated(self):
        # ids too short for unambiguous substring replacement are rejected
        captured = [
            {"url": "https://x/api/sessions", "data": [{"id": 42, "date": "2026-03-25"}]},
            {"url": "https://x/api/sessions/42/shots", "data": SHOTS_PAYLOAD},
        ]
        assert build_manifest(captured, "2026-03-25") is None

    def test_shot_bearing_payload_not_used_as_session_list(self):
        # A shots payload with ids and date strings must not masquerade as the
        # session list (its ids are shot ids, not session ids).
        shots_with_ids = {
            "shots": [
                {"id": 555555, "ballSpeed": 118.4, "createdAt": "2026-03-25T14:05:00Z"},
            ]
        }
        captured = [
            {"url": "https://x/api/sessions/987654/shots", "data": shots_with_ids},
            CAPTURED[0],
        ]
        manifest = build_manifest(captured, "2026-03-25")
        assert manifest is not None
        assert manifest["session_list_url"] == CAPTURED[0]["url"]
        # Templated with the session id from the list, not the shot id
        assert "{session_id}" in manifest["shot_url_templates"][0]
        assert "555555" not in manifest["shot_url_templates"][0]


# ---------------------------------------------------------------------------
# Manifest persistence + URL substitution
# ---------------------------------------------------------------------------


class TestManifestPersistence:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rcloud_api, "MANIFEST_PATH", tmp_path / "api_manifest.json")
        manifest = build_manifest(CAPTURED, "2026-03-25")
        save_manifest(manifest)
        assert load_manifest() == manifest

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rcloud_api, "MANIFEST_PATH", tmp_path / "nope.json")
        assert load_manifest() is None

    def test_corrupt_file_returns_none(self, tmp_path, monkeypatch):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        monkeypatch.setattr(rcloud_api, "MANIFEST_PATH", path)
        assert load_manifest() is None

    def test_incomplete_manifest_returns_none(self, tmp_path, monkeypatch):
        path = tmp_path / "incomplete.json"
        path.write_text(json.dumps({"session_list_url": "https://x"}))
        monkeypatch.setattr(rcloud_api, "MANIFEST_PATH", path)
        assert load_manifest() is None


class TestShotUrlsForSession:
    def test_substitution(self):
        manifest = {"shot_url_templates": ["https://x/api/sessions/{session_id}/shots"]}
        assert shot_urls_for_session(manifest, "12345") == ["https://x/api/sessions/12345/shots"]

    def test_numeric_id_coerced(self):
        manifest = {"shot_url_templates": ["https://x/{session_id}"]}
        assert shot_urls_for_session(manifest, 999) == ["https://x/999"]
