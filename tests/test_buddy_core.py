"""Tests for tools/buddy_core/core.py — the shared daemon library."""

import asyncio
import struct
import zlib


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


def test_to_payload_app_tag(fresh_state):
    # No tag by default → field omitted (back-compatible single-feed firmware).
    assert "app" not in fresh_state.to_payload()
    # Set → emitted on every heartbeat for dual-feed routing.
    fresh_state.app = "cursor"
    assert fresh_state.to_payload()["app"] == "cursor"
    assert fresh_state.to_payload()["app"] == "cursor"


def test_to_payload_prompt_omitted_when_none(fresh_state):
    assert "prompt" not in fresh_state.to_payload()
    fresh_state.prompt = {"id": "r1", "tool": "Bash", "hint": "rm -rf"}
    assert fresh_state.to_payload()["prompt"]["tool"] == "Bash"


def test_to_payload_carries_hud_fields(fresh_state):
    # HUD metrics are state, not one-shot — every heartbeat carries them.
    fresh_state.context_pct = 62
    fresh_state.model = "Opus 4.7"
    fresh_state.limit_5h = 38
    fresh_state.limit_7d = 13
    fresh_state.session_ms = 1234567
    p = fresh_state.to_payload()
    assert p["context_pct"] == 62
    assert p["model"] == "Opus 4.7"
    assert p["limit_5h"] == 38
    assert p["limit_7d"] == 13
    assert p["session_ms"] == 1234567
    # Still present (unchanged) on the next heartbeat.
    assert fresh_state.to_payload()["context_pct"] == 62


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


# ─── keyboard relay: name → kVK (cmd:key) ──────────────────────────────

def test_kvk_for_special_keys(core):
    assert core.kvk_for("enter") == 0x24
    assert core.kvk_for("return") == 0x24
    assert core.kvk_for("backspace") == 0x33
    assert core.kvk_for("tab") == 0x30
    assert core.kvk_for("esc") == 0x35
    assert core.kvk_for("left") == 0x7B
    assert core.kvk_for("up") == 0x7E


def test_kvk_for_letters_and_digits(core):
    assert core.kvk_for("a") == 0x00
    assert core.kvk_for("c") == 0x08   # ⌘C lands on the real C keycode
    assert core.kvk_for("z") == 0x06
    assert core.kvk_for("1") == 0x12
    assert core.kvk_for("0") == 0x1D


def test_kvk_for_is_case_insensitive_and_unknown_is_none(core):
    assert core.kvk_for("UP") == 0x7E
    assert core.kvk_for("ENTER") == 0x24
    assert core.kvk_for("nope") is None
    assert core.kvk_for("") is None


def test_mod_mask_covers_relay_modifiers(core):
    assert core._MOD_MASK["cmd"] == 0x100000
    assert core._MOD_MASK["shift"] == 0x020000
    assert core._MOD_MASK["opt"] == 0x080000
    assert core._MOD_MASK["ctrl"] == 0x040000


# ─── screenshot PNG writer ─────────────────────────────────────────────

def test_rgb565_to_rgb888_pure_colors(core):
    # red 0xF800, green 0x07E0, blue 0x001F (little-endian byte pairs)
    raw = bytes([0x00, 0xF8, 0xE0, 0x07, 0x1F, 0x00])
    rgb = core._rgb565_to_rgb888(raw, 3, 1)
    assert rgb[0:3] == bytes([0xFF, 0x00, 0x00])   # red
    assert rgb[3:6] == bytes([0x00, 0xFF, 0x00])   # green
    assert rgb[6:9] == bytes([0x00, 0x00, 0xFF])   # blue


def test_write_png_is_valid(core, tmp_path):
    w, h = 4, 3
    rgb = bytes([10, 20, 30]) * (w * h)
    out = tmp_path / "shot.png"
    core._write_png(str(out), w, h, rgb)
    data = out.read_bytes()
    # PNG signature
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # first chunk is IHDR with our dimensions
    assert data[12:16] == b"IHDR"
    iw, ih = struct.unpack(">II", data[16:24])
    assert (iw, ih) == (w, h)
    # IDAT decompresses to h rows of (1 filter byte + w*3)
    idat_start = data.find(b"IDAT") + 4
    idat_len = struct.unpack(">I", data[idat_start - 8:idat_start - 4])[0]
    raw = zlib.decompress(data[idat_start:idat_start + idat_len])
    assert len(raw) == h * (1 + w * 3)


# ─── BleWriter.write fast-skip ─────────────────────────────────────────

def test_ble_write_never_reconnects_inline(core):
    """write() must skip an offline peer instantly. The old inline
    ensure_connected() ran a BleakScanner.discover (SCAN_TIMEOUT=8s) and
    MultiBleWriter gathers all peers — one absent stick stalled every
    heartbeat emit (serial included), so permission prompts reached the
    Tab5 after the 8s approval window had burned. reconnect_loop owns
    reconnection."""
    w = core.BleWriter("Claude-TEST")
    w.client = None

    async def boom():
        raise AssertionError("write() must not call ensure_connected()")

    w.ensure_connected = boom
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(w.write({"running": 0}))   # returns, no scan
    finally:
        loop.close()
