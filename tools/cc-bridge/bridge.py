#!/usr/bin/env python3
"""
cc-bridge — Claude Code (CLI) ↔ M5StickC buddy daemon.

Long-running process. Listens on a Unix socket for hook events forwarded
by tools/cc-bridge/hook.py, aggregates them into the heartbeat schema
documented in REFERENCE.md, and writes the resulting JSON to the stick's
Nordic UART RX characteristic over BLE.

Stick firmware needs zero changes — it's the same wire protocol Claude
Desktop already speaks. We're just a new producer.

Lifecycle:
  - Stick must be bonded with macOS first (System Settings → Bluetooth,
    enter the 6-digit passkey shown on the stick screen). This is a
    one-time UX dance that bleak's connect path expects.
  - Daemon scans for advertising name "Claude-*", connects to NUS RX
    characteristic, and stays connected. On disconnect (stick power
    off, desktop took over, etc.), it reconnects with backoff.
  - Hook events arrive as one JSON object per line on the Unix socket.
    Each event mutates BuddyState; after every mutation we re-emit a
    fresh heartbeat. A 10s keepalive heartbeat fires regardless.

Run manually:
  python3 tools/cc-bridge/bridge.py
Run as launchd daemon:
  see tools/cc-bridge/install.sh
"""

import os
import sys
import time
import pathlib

# Allow `from buddy_core import ...` AND `from dashboard import ...`
# when launched as a standalone script via launchd (where cwd ≠ here).
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from buddy_core import run, BuddyState
from dashboard import start_dashboard, DEFAULT_PORT as DASH_DEFAULT_PORT

# ─── config ────────────────────────────────────────────────────────────
SOCKET_PATH = os.environ.get("CC_BRIDGE_SOCKET", "/tmp/cc-bridge.sock")
DEVICE_PREFIX = os.environ.get("CC_BRIDGE_DEVICE_PREFIX", "Claude-")
LOG_PATH = os.environ.get(
    "CC_BRIDGE_LOG", str(pathlib.Path.home() / "Library/Logs/cc-bridge.log")
)
PTT_KEYCODE = int(os.environ.get("CC_BRIDGE_PTT_KEYCODE", "61"))  # right Option
# "tap" = Typeless toggle (default); "hold" = Doubao 长按 / classic PTT;
# "double_tap" = Doubao 免按. See tools/buddy_core/core.py:make_on_stick_line.
PTT_MODE = os.environ.get("CC_BRIDGE_PTT_MODE", "tap")

# cc-bridge talks to the firmware's debug service (unencrypted) instead
# of the encrypted NUS that Claude Desktop uses. Same line-JSON protocol;
# the firmware mirrors notifies to both characteristics. This avoids the
# macOS bleak ↔ ESP32 secure-pairing instability that kept dropping the
# encrypted link mid-session.

# Tools that should never trigger an approve prompt on the stick:
# pure interactive (AskUserQuestion, *PlanMode) and planning/state-only
# (TodoWrite, Task*). These don't touch the system, and AskUserQuestion
# in particular IS the asking mechanism — gating it is a logic loop.
SAFE_TOOLS = {
    "AskUserQuestion",
    "ExitPlanMode",
    "EnterPlanMode",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "TaskOutput",
    "TaskStop",
}


# ─── hook event → state mutations ──────────────────────────────────────
def apply_event(state: BuddyState, ev: dict) -> bool:
    """Mutate state from a Claude Code hook event. Returns True if the
    payload changed materially (= we should re-emit immediately)."""
    name = ev.get("hook_event_name") or ev.get("event") or ""
    sid = ev.get("session_id") or ev.get("sessionId") or "anon"
    changed = False

    # Universal sound dispatch — every recognized event sets pending_play
    # to the lowercase event name; firmware looks up /sounds/<name>.wav on
    # LittleFS and plays it. Unknown names silently no-op on firmware
    # (sound.cpp only loads .wav files present on disk). Specific event
    # handlers below MAY override pending_play with a more specific clip
    # name if that turns out useful, but for now 1:1 mapping is enough.
    if name:
        state.pending_play = name.lower()
        changed = True  # heartbeat must emit so firmware gets the cue

    # Semantics matter — Claude Code's `Stop` is "assistant turn ended", NOT
    # "session terminated". Don't decrement `total` there. And don't set
    # `completed`; that's reserved for level-ups in the upstream protocol
    # and would otherwise fire CELEBRATE on every turn end.
    if name == "SessionStart":
        if sid not in state._sessions:
            state._sessions[sid] = {"running": False}
            state.total += 1
            changed = True
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
        # Even if we never saw a SessionStart, treat this as one.
        elif sid not in state._sessions:
            state._sessions[sid] = {"running": True}
            state.total += 1
            state.running += 1
        prompt = ev.get("prompt") or ev.get("user_prompt") or ""
        if prompt:
            state.add_entry(f"you: {prompt}")
        state.msg = "thinking…"
        changed = True

    elif name == "Stop":
        # Assistant done responding (this turn). Session still open.
        s = state._sessions.get(sid)
        if s and s.get("running"):
            s["running"] = False
            state.running = max(0, state.running - 1)
            state.msg = "ready"
            changed = True

    elif name == "PreToolUse":
        tool = ev.get("tool_name") or "tool"
        state.msg = f"running: {tool}"
        ti = ev.get("tool_input") or {}
        # Truncate command-y inputs if present.
        hint = ti.get("command") or ti.get("description") or ti.get("file_path") or ""
        line = f"{tool} {hint}".strip()
        state.add_entry(line)
        changed = True

    elif name == "PostToolUse":
        tool = ev.get("tool_name") or "tool"
        state.msg = f"done: {tool}"
        changed = True

    elif name in ("PermissionRequest", "Notification"):
        # Permission ask blocks the session — surface it.
        if name == "PermissionRequest" or "permission" in (ev.get("message") or "").lower():
            tool = ev.get("tool_name") or ev.get("tool") or "tool"
            # Asking the user to approve being asked is a logic loop.
            # Same for pure planning/state tools — they don't touch the
            # system, so don't burn an approve prompt on them.
            if tool in SAFE_TOOLS:
                pass
            else:
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
                # "Claude is waiting for your input" means the assistant
                # turn ended and Claude is idle-waiting — semantically a
                # Stop. Without clearing the session's running flag the
                # stale running count makes firmware mapState() show
                # BUSY while the bubble says "waiting for your input".
                if "waiting for your input" in msg.lower():
                    s = state._sessions.get(sid)
                    if s and s.get("running"):
                        s["running"] = False
                        state.running = max(0, state.running - 1)
                changed = True

    elif name == "PostCompact":
        state.add_entry("compacted")
        changed = True

    return changed


if __name__ == "__main__":
    # Dashboard port: env var CC_BRIDGE_DASH_PORT overrides; 0 disables.
    # Default DEFAULT_PORT (8765) is fine on macOS where launchd doesn't
    # share that range with system services. Bound to 127.0.0.1 only —
    # don't expose the speaker/screen remote-control to the network.
    DASH_PORT = int(os.environ.get("CC_BRIDGE_DASH_PORT", str(DASH_DEFAULT_PORT)))

    def _on_loop_start(ble, loop, log):
        if DASH_PORT > 0:
            start_dashboard(ble, loop, log=log, port=DASH_PORT)
        else:
            log.info("dashboard disabled (CC_BRIDGE_DASH_PORT=0)")

    run(
        name="cc-bridge",
        socket_path=SOCKET_PATH,
        log_path=LOG_PATH,
        device_prefix=DEVICE_PREFIX,
        apply_event=apply_event,
        ptt_keycode=PTT_KEYCODE,
        ptt_mode=PTT_MODE,
        keepalive_s=10.0,
        rtc_sync_on_connect=False,  # Claude Desktop handles RTC for cc-bridge
        on_loop_start=_on_loop_start,
    )
