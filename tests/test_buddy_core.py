"""Tests for tools/buddy_core/core.py — the shared daemon library."""

import asyncio


# ─── BuddyState.to_payload ─────────────────────────────────────────────

def test_to_payload_basic_fields(fresh_state):
    fresh_state.total = 2
    fresh_state.running = 1
    fresh_state.waiting = 0
    fresh_state.msg = "thinking…"
    fresh_state.tokens = 1234
    p = fresh_state.to_payload()
    assert p["total"] == 2
    assert p["running"] == 1
    assert p["waiting"] == 0
    assert p["msg"] == "thinking…"
    assert p["tokens"] == 1234


def test_to_payload_prompt_omitted_when_none(fresh_state):
    assert "prompt" not in fresh_state.to_payload()
    fresh_state.prompt = {"id": "r1", "tool": "Bash", "hint": "rm -rf"}
    assert fresh_state.to_payload()["prompt"]["tool"] == "Bash"


def test_to_payload_completed_is_one_shot(fresh_state):
    fresh_state.completed = True
    assert fresh_state.to_payload().get("completed") is True
    # Cleared after the first read — the firmware should only celebrate once.
    assert "completed" not in fresh_state.to_payload()


def test_to_payload_pending_play_is_one_shot(fresh_state):
    fresh_state.pending_play = "permissionrequest"
    assert fresh_state.to_payload()["play"] == "permissionrequest"
    assert "play" not in fresh_state.to_payload()
    assert fresh_state.pending_play is None


# ─── BuddyState.add_entry ──────────────────────────────────────────────

def test_add_entry_newest_first(fresh_state):
    fresh_state.add_entry("first")
    fresh_state.add_entry("second")
    assert fresh_state.entries[0] == "second"
    assert fresh_state.entries[1] == "first"


def test_add_entry_truncates_to_91_chars(fresh_state):
    fresh_state.add_entry("x" * 200)
    assert len(fresh_state.entries[0]) == 91


def test_add_entry_caps_at_8(fresh_state):
    for i in range(20):
        fresh_state.add_entry(f"entry {i}")
    assert len(fresh_state.entries) == 8
    assert fresh_state.entries[0] == "entry 19"


# ─── _safe_set ─────────────────────────────────────────────────────────

def test_safe_set_is_idempotent(core):
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        core._safe_set(fut, "allow")
        assert fut.result() == "allow"
        # A redelivered BLE notification must not raise InvalidStateError.
        core._safe_set(fut, "deny")
        assert fut.result() == "allow"
    finally:
        loop.close()


# ─── _MOD_FLAGS ────────────────────────────────────────────────────────

def test_mod_flags_cover_modifier_keycodes(core):
    # The flag-mask modifier kVK codes (Caps Lock 57 is intentionally absent —
    # it isn't relayed as a CGEventFlagsChanged modifier).
    for kc in (54, 55, 56, 58, 59, 60, 61, 62, 63):
        assert kc in core._MOD_FLAGS
    assert 57 not in core._MOD_FLAGS
    # Right Option (61, the PTT default) is kCGEventFlagMaskAlternate.
    assert core._MOD_FLAGS[61] == 0x080000
