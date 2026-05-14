"""Tests for tools/cc-bridge/statusline_hud.py — the statusline proxy.

Covers the pure metric-extraction logic. The socket forwarding and the
omc-hud.mjs chaining are I/O side effects exercised by hand, not unit-tested.
"""


def test_extract_metrics_full_payload(statusline_hud):
    m = statusline_hud._extract_metrics({
        "model": {"display_name": "Opus 4.7"},
        "context_window": {
            "used_percentage": 62.4,
            "current_usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 45000,
                "cache_creation_input_tokens": 2000,
            },
        },
        "rate_limits": {
            "five_hour": {"used_percentage": 38},
            "seven_day": {"used_percentage": 12.7},
        },
    })
    assert m["hook_event_name"] == "hud"
    assert m["context_pct"] == 62          # rounded
    assert m["tokens"] == 48000            # summed across the three buckets
    assert m["limit_5h"] == 38
    assert m["limit_7d"] == 13             # rounded
    assert m["model"] == "Opus 4.7"


def test_extract_metrics_empty_payload_is_all_zeroes(statusline_hud):
    m = statusline_hud._extract_metrics({})
    assert m == {
        "hook_event_name": "hud", "context_pct": 0, "tokens": 0,
        "limit_5h": 0, "limit_7d": 0, "model": "", "session_ms": 0,
    }


def test_extract_metrics_tolerates_missing_subfields(statusline_hud):
    # context_window present but no current_usage / used_percentage.
    m = statusline_hud._extract_metrics({"context_window": {}})
    assert m["tokens"] == 0
    assert m["context_pct"] == 0


def test_session_ms_zero_when_no_transcript(statusline_hud):
    assert statusline_hud._session_ms(None) == 0
    assert statusline_hud._session_ms("/nonexistent/transcript.jsonl") == 0
