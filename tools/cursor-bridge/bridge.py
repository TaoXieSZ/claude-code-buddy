#!/usr/bin/env python3
"""
cursor-bridge — Cursor IDE ↔ M5StickC buddy daemon.

Long-running process. Listens on a Unix socket for hook events forwarded
by tools/cursor-bridge/cursor_hook.js, aggregates them into the heartbeat
schema documented in REFERENCE.md, and writes the resulting JSON to the
stick's Nordic UART RX characteristic over BLE.

Lifecycle:
  - Stick must be bonded with macOS first (System Settings → Bluetooth,
    enter the 6-digit passkey shown on the stick screen). This is a
    one-time UX dance that bleak's connect path expects.
  - Daemon scans for advertising name "Cursor-*", connects to NUS RX
    characteristic, and stays connected. On disconnect (stick power
    off, desktop took over, etc.), it reconnects with backoff. The
    Cursor- prefix is what a stick flashed with the
    `m5stickc-plus2-cursor` PlatformIO env advertises — distinct from
    cc-bridge's `Claude-` so the two daemons can coexist on the same
    Mac without racing for the same advertisement.
  - Hook events arrive as one JSON object per line on the Unix socket.
    cursor_hook.js translates the Cursor hook schema (sessionStart,
    beforeSubmitPrompt, beforeShellExecution, ...) into Claude-Code-shaped
    events that apply_event() already understands. Each event mutates
    BuddyState; after every mutation we re-emit a fresh heartbeat. A 2s
    keepalive heartbeat fires regardless.

Pin to a specific stick when running cc-bridge and cursor-bridge on the
same Mac. With the -cursor firmware variant the default prefix is
"Cursor-" so the two bridges naturally don't collide; this env var
is only needed if you flashed both sticks with the same firmware
variant and need to pin by MAC suffix:
    launchctl setenv CURSOR_BRIDGE_DEVICE_PREFIX Cursor-6DE2
    launchctl kickstart -k gui/$(id -u)/com.cursor-bridge

Run manually:
  python3 tools/cursor-bridge/bridge.py
Run as launchd daemon:
  see tools/cursor-bridge/install.sh
"""

import os
import sys
import time
import asyncio
import pathlib

# Allow `from buddy_core import ...` when launched as a standalone script.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from buddy_core import run, BuddyState

# ─── config ────────────────────────────────────────────────────────────
SOCKET_PATH = os.environ.get("CURSOR_BRIDGE_SOCKET", "/tmp/cursor-bridge.sock")
DEVICE_PREFIX = os.environ.get("CURSOR_BRIDGE_DEVICE_PREFIX", "Cursor-")
LOG_PATH = os.environ.get(
    "CURSOR_BRIDGE_LOG", str(pathlib.Path.home() / "Library/Logs/cursor-bridge.log")
)
PTT_KEYCODE = int(os.environ.get("CURSOR_BRIDGE_PTT_KEYCODE", "61"))  # right Option
# "tap" = Typeless toggle (default); "hold" = Doubao 长按 / classic PTT;
# "double_tap" = Doubao 免按. See tools/buddy_core/core.py:make_on_stick_line.
PTT_MODE = os.environ.get("CURSOR_BRIDGE_PTT_MODE", "tap")

# cursor-bridge talks to the firmware's debug service (unencrypted) just
# like cc-bridge — the firmware mirrors notifies to both the encrypted
# NUS Claude Desktop uses and this debug one. This avoids the macOS
# bleak ↔ ESP32 secure-pairing instability that kept dropping the
# encrypted link mid-session.

# Sessions older than this with no events are reaped on the next tick.
# Cursor IDE doesn't fire SessionEnd hooks (verified empirically), so without
# this `state.total` and `state.running` would only ever grow; a Cursor
# window that's been idle for an hour shouldn't keep counting against the
# active-session badge on the stick.
STALE_SESSION_SEC = 600  # 10 min


# ─── hook event → state mutations ──────────────────────────────────────
def apply_event(state: BuddyState, ev: dict) -> bool:
    """Mutate state from a Cursor hook event. Returns True if the payload
    changed materially (= we should re-emit immediately)."""
    name = ev.get("hook_event_name") or ev.get("event") or ""
    sid = ev.get("session_id") or ev.get("sessionId") or "anon"
    changed = False

    now = time.monotonic()

    if name == "SessionStart":
        if sid not in state._sessions:
            state._sessions[sid] = {"running": False, "last_seen": now}
            state.total += 1
            changed = True
        else:
            state._sessions[sid]["last_seen"] = now
        state.add_entry("session start")

    elif name == "SessionEnd":
        if state._sessions.pop(sid, None):
            state.total = max(0, state.total - 1)
            state.add_entry("session ended")
            changed = True

    elif name == "UserPromptSubmit":
        # User just submitted → model about to think.
        if sid in state._sessions and not state._sessions[sid].get("running"):
            state._sessions[sid]["running"] = True
            state.running += 1
        # Cursor IDE doesn't fire SessionStart hooks (verified empirically),
        # so the very first event we ever see for a session is usually
        # UserPromptSubmit. Treat it as the session-start signal.
        elif sid not in state._sessions:
            state._sessions[sid] = {"running": True, "last_seen": now}
            state.total += 1
            state.running += 1
        state._sessions[sid]["last_seen"] = now
        prompt = ev.get("prompt") or ev.get("user_prompt") or ""
        if prompt:
            state.add_entry(f"you: {prompt}")
        state.msg = "thinking…"
        changed = True

    elif name == "Stop":
        # Assistant done responding (this turn). Session still open.
        s = state._sessions.get(sid)
        if s:
            s["last_seen"] = now
            if s.get("running"):
                s["running"] = False
                state.running = max(0, state.running - 1)
                state.msg = "ready"
                changed = True
        # afterAgentResponse (Cursor) attaches output_tokens + text. Accumulate
        # tokens into state.tokens / state.tokens_today so the buddy's
        # display advances; push the assistant reply head into entries so the
        # transcript scroll has something more interesting than tool names.
        ot = ev.get("output_tokens")
        if isinstance(ot, int) and ot > 0:
            state.tokens += ot
            state.tokens_today += ot
            changed = True
        txt = ev.get("text") or ""
        if txt:
            # Single-line collapse — REFERENCE.md caps each entry at ~91
            # chars; add_entry() truncates further. Strip leading whitespace
            # so the indent doesn't waste display columns.
            state.add_entry(f"buddy: {txt.replace(chr(10), ' ').strip()}")
            changed = True

    elif name == "PreToolUse":
        tool = ev.get("tool_name") or "tool"
        state.msg = f"running: {tool}"
        ti = ev.get("tool_input") or {}
        # Truncate command-y inputs if present.
        hint = ti.get("command") or ti.get("description") or ti.get("file_path") or ""
        line = f"{tool} {hint}".strip()
        state.add_entry(line)
        if sid in state._sessions:
            state._sessions[sid]["last_seen"] = now
        changed = True

    elif name == "PostToolUse":
        tool = ev.get("tool_name") or "tool"
        if ev.get("failure"):
            err = (ev.get("error") or "").strip()
            state.msg = f"failed: {tool}"
            # Push the failure into entries with a leading '!' marker so the
            # transcript scroll on the stick visually distinguishes failed
            # tool calls. Truncation is handled by add_entry().
            state.add_entry(f"!fail {tool} {err}".strip())
        else:
            state.msg = f"done: {tool}"
        if sid in state._sessions:
            state._sessions[sid]["last_seen"] = now
        changed = True

    elif name in ("PermissionRequest", "Notification"):
        # Permission ask blocks the session — surface it.
        if name == "PermissionRequest" or "permission" in (ev.get("message") or "").lower():
            tool = ev.get("tool_name") or ev.get("tool") or "tool"
            pid = ev.get("request_id") or ev.get("id") or f"req_{int(time.time())}"
            state.waiting = max(state.waiting, 1)
            state.prompt = {
                "id": pid,
                "tool": tool,
                "hint": (ev.get("message") or ev.get("hint") or "")[:120],
            }
            state.msg = f"approve: {tool}"
            changed = True
        else:
            # Generic notification — show its message line.
            msg = ev.get("message") or ev.get("title") or ""
            if msg:
                state.msg = msg[:120]
                state.add_entry(msg)
                changed = True

    elif name == "PostCompact":
        state.add_entry("compacted")
        changed = True

    return changed


# ─── cursor-specific: stale-session reaper ─────────────────────────────
async def reaper_loop(state: BuddyState, dirty: asyncio.Event):
    """Periodically drop sessions with no recent activity, recompute counters.

    Cursor IDE doesn't fire SessionEnd hooks, so without this the counters
    only ever grow. Passed to run() via extra_tasks.
    """
    import logging
    log = logging.getLogger("cursor-bridge")
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        stale = [
            sid for sid, s in state._sessions.items()
            if now - s.get("last_seen", now) > STALE_SESSION_SEC
        ]
        if not stale:
            continue
        for sid in stale:
            log.info("reaping stale session %s (idle %ds)",
                     sid[:8], int(now - state._sessions[sid].get("last_seen", now)))
            state._sessions.pop(sid, None)
        # Recompute counters from the post-reap session set so they can't drift.
        state.total = len(state._sessions)
        state.running = sum(1 for s in state._sessions.values() if s.get("running"))
        dirty.set()


def _cursor_log_fmt(payload: dict) -> str:
    """Custom emit log line for cursor-bridge (includes token counts)."""
    return (
        f"running={payload.get('running', 0)} waiting={payload.get('waiting', 0)} "
        f"tokens={payload.get('tokens', 0)}/{payload.get('tokens_today', 0)} "
        f"prompt={(payload.get('prompt', {}) or {}).get('id', '-')} "
        f"msg={payload.get('msg', '')[:40]} "
        f"entries[0]={(payload.get('entries', [''])[:1] or [''])[0][:50]}"
    )


if __name__ == "__main__":
    run(
        name="cursor-bridge",
        socket_path=SOCKET_PATH,
        log_path=LOG_PATH,
        device_prefix=DEVICE_PREFIX,
        apply_event=apply_event,
        ptt_keycode=PTT_KEYCODE,
        ptt_mode=PTT_MODE,
        keepalive_s=2.0,
        rtc_sync_on_connect=True,   # no Claude Desktop in the loop for cursor
        extra_tasks=[reaper_loop],
        log_fmt=_cursor_log_fmt,
    )
