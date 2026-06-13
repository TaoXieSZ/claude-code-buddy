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

import asyncio
import logging
import os
import sys
import time
import pathlib

# Allow `from buddy_core import ...` AND `from dashboard import ...`
# when launched as a standalone script via launchd (where cwd ≠ here).
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from buddy_core import run, BuddyState
from buddy_core.frame_server import FrameServer
from dashboard import start_dashboard, DEFAULT_PORT as DASH_DEFAULT_PORT
from rover_dashboard import (
    start_rover_dashboard,
    DEFAULT_PORT as ROVER_DEFAULT_PORT,
    DEFAULT_TARGET_PREFIX as ROVER_DEFAULT_PREFIX,
)

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
# Wired Tab5 peer (USB-CDC serial). Empty = disabled. The same NDJSON
# heartbeat goes down the wire; {"cmd":"permission"...} / btn lines come back.
TAB5_SERIAL = os.environ.get("CC_BRIDGE_TAB5_SERIAL", "")

# cc-bridge talks to the firmware's debug service (unencrypted) instead
# of the encrypted NUS that Claude Desktop uses. Same line-JSON protocol;
# the firmware mirrors notifies to both characteristics. This avoids the
# macOS bleak ↔ ESP32 secure-pairing instability that kept dropping the
# encrypted link mid-session.

# Tools that should never trigger an approve prompt on the stick:
# pure interactive (AskUserQuestion, *PlanMode) and planning/state-only
# (TodoWrite, Task*). These don't touch the system, and AskUserQuestion
# in particular IS the asking mechanism — gating it is a logic loop.
# Sessions idle for longer than this are dropped by reaper_loop and the
# total/running counters are recomputed from the surviving set. Safety net
# against dropped Stop events (which would otherwise leave state.running
# stuck > 0 forever). 10 min mirrors cursor-bridge's threshold; see
# openspec change 0004-cc-bridge-session-reaper.
STALE_SESSION_SEC = 600

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
def _clear_waiting(state: BuddyState) -> None:
    """Reset the permission-blocked counters.

    The async apply_event path sets state.waiting/state.prompt on a
    PermissionRequest but, unlike the synchronous hook_permission.py path
    (core.py:_handle_wait_permission), never had a place to clear them — so
    W stuck at 1 on the HUD forever. Called when the turn progresses
    (UserPromptSubmit / PreToolUse / Stop), which means the user is no
    longer being blocked on. See change 0001-heartbeat-counter-lifecycle.
    """
    state.waiting = 0
    state.prompt = None


# ─── RoverC dance linkage ──────────────────────────────────────────────
# apply_event has a fixed (state, ev) signature and no BLE handle, so the
# rover writer/loop/prefix are stashed module-level by _on_loop_start once
# the BLE loop is up. apply_event then fires a one-shot dance on Stop /
# PostToolUse. None until wired (single-stick / no-rover deployments).
_ROVER_BLE = None
_ROVER_LOOP = None
_ROVER_PREFIX = None
_rover_last_dance = 0.0
# PostToolUse fires many times per turn — cap auto-dances to one per N s so
# the rover doesn't churn nonstop (and drain its battery). 0 disables all
# event-linked dancing.
ROVER_DANCE_COOLDOWN_S = float(os.environ.get("CC_BRIDGE_ROVER_DANCE_COOLDOWN", "8"))


def _rover_dance(ms: int, respect_cooldown: bool = True) -> None:
    """Schedule a one-shot rover dance from sync code (apply_event).

    No-op unless a rover peer is wired. Targets only the rover peer via
    MultiBleWriter.write_to (falls back to broadcast write for a single
    BleWriter). Cooldown guards against PostToolUse spam.
    """
    global _rover_last_dance
    if _ROVER_BLE is None or _ROVER_LOOP is None or _ROVER_PREFIX is None:
        return
    if ROVER_DANCE_COOLDOWN_S <= 0:
        return  # disabled
    now = time.time()
    if respect_cooldown and (now - _rover_last_dance) < ROVER_DANCE_COOLDOWN_S:
        return
    _rover_last_dance = now
    payload = {"cmd": "dance", "ms": int(ms)}
    try:
        coro = (_ROVER_BLE.write_to(_ROVER_PREFIX, payload)
                if hasattr(_ROVER_BLE, "write_to") else _ROVER_BLE.write(payload))
        asyncio.run_coroutine_threadsafe(coro, _ROVER_LOOP)
        logging.getLogger("cc-bridge").info("rover dance ms=%d", int(ms))
    except Exception:
        pass


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
    # `hud` is pure metric telemetry from the statusline proxy — no sound
    # cue (there's no hud.wav and a blip on every statusline render would
    # be maddening).
    if name and name != "hud":
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
        # User just submitted → model about to think. A new turn means any
        # prior permission prompt is no longer blocking.
        _clear_waiting(state)
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
        # Turn ended → no longer blocked on the user.
        _clear_waiting(state)
        s = state._sessions.get(sid)
        if s and s.get("running"):
            s["running"] = False
            state.running = max(0, state.running - 1)
            state.msg = "ready"
            changed = True
            _rover_dance(3000, respect_cooldown=False)  # end-of-turn celebration

    elif name == "PreToolUse":
        # A tool starting means a pending permission was granted.
        _clear_waiting(state)
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
        _rover_dance(1200)  # short wiggle; cooldown caps the per-turn frequency

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

    elif name == "PreCompact":
        # Compaction can take tens of seconds — explain the pause instead of
        # leaving stale state on screen. openspec change 0005.
        state.msg = "compacting…"
        state.add_entry("compacting context…")
        changed = True

    elif name == "PostCompact":
        state.msg = "compacted"
        state.add_entry("compacted")
        changed = True

    elif name == "SubagentStart":
        agent = ev.get("agent_type") or ev.get("subagent_type") or ""
        state.add_entry(f"subagent: {agent}" if agent else "subagent started")
        changed = True

    elif name == "SubagentStop":
        state.add_entry("subagent done")
        changed = True

    elif name == "PostToolUseFailure":
        tool = ev.get("tool_name") or "tool"
        err = (ev.get("error") or ev.get("message") or "")[:60]
        state.msg = f"failed: {tool}"
        state.add_entry(f"✗ {tool} {err}".rstrip())
        changed = True

    elif name == "hud":
        # Live statusline metrics from tools/cc-bridge/statusline_hud.py.
        # Pure telemetry — copy each present field onto state, leave the
        # session/running/waiting lifecycle untouched. Missing fields are
        # left at their previous value so a partial payload doesn't zero
        # things out. See openspec change 0002-hud-metrics-integration.
        for fld in ("context_pct", "tokens", "limit_5h", "limit_7d",
                    "session_ms"):
            v = ev.get(fld)
            if isinstance(v, int):
                setattr(state, fld, v)
        m = ev.get("model")
        if isinstance(m, str) and m:
            state.model = m
        changed = True

    # Stamp last_seen on the surviving session record for the reaper.
    # SessionEnd has already popped the record; hud events carry no real
    # sid (it's "anon" or "?") so they won't match anything in the map.
    # See openspec change 0004-cc-bridge-session-reaper.
    if sid in state._sessions:
        state._sessions[sid]["last_seen"] = time.monotonic()

    return changed


# ─── stale-session reaper ──────────────────────────────────────────────
def _reap_stale_sessions(state: BuddyState,
                         now: float | None = None) -> bool:
    """Drop sessions whose `last_seen` is older than STALE_SESSION_SEC, then
    recompute `state.total` / `state.running` from the surviving session map.

    Returns True if anything changed (counters moved or a session was
    dropped). Pure-sync helper — called from reaper_loop and unit-tested
    directly without spinning the event loop. `now` defaults to
    time.monotonic(); tests inject a fixed value.

    Recompute (not decrement) is the part that actually fixes drift —
    counters can't underflow / overflow because they're rebuilt from the
    truthful set on every reap.
    """
    if now is None:
        now = time.monotonic()
    stale = [
        sid for sid, s in state._sessions.items()
        if now - s.get("last_seen", now) > STALE_SESSION_SEC
    ]
    for sid in stale:
        state._sessions.pop(sid, None)
    new_total = len(state._sessions)
    new_running = sum(1 for s in state._sessions.values() if s.get("running"))
    changed = bool(stale) or new_total != state.total or new_running != state.running
    state.total = new_total
    state.running = new_running
    return changed


async def reaper_loop(state: BuddyState, dirty: asyncio.Event) -> None:
    """Wake every 60 s, reap stale sessions, signal dirty if anything moved.

    Passed to `run()` via `extra_tasks`. The 60-s cadence is fine even with
    STALE_SESSION_SEC=600 — at worst the user sees a 60-s delay before the
    HUD updates after a drift, which is invisible against the 10-min stale
    window.
    """
    import logging
    log = logging.getLogger("cc-bridge")
    while True:
        await asyncio.sleep(60)
        before = len(state._sessions)
        if _reap_stale_sessions(state):
            after = len(state._sessions)
            log.info("reaper: dropped %d stale session(s); running=%d total=%d",
                     before - after, state.running, state.total)
            dirty.set()


if __name__ == "__main__":
    # Dashboard port: env var CC_BRIDGE_DASH_PORT overrides; 0 disables.
    # Default DEFAULT_PORT (8765) is fine on macOS where launchd doesn't
    # share that range with system services. Bound to 127.0.0.1 only —
    # don't expose the speaker/screen remote-control to the network.
    DASH_PORT = int(os.environ.get("CC_BRIDGE_DASH_PORT", str(DASH_DEFAULT_PORT)))

    # StackChan camera-stream ingress port. Matches the default in
    # wifi_secrets.ini consumed by the firmware build. Set to 0 to disable
    # the listener (e.g. on machines that don't run the StackChan).
    # openspec change 0003-stackchan-camera-gestures.
    FRAME_PORT = int(os.environ.get("CC_BRIDGE_FRAME_PORT", "8770"))
    # Number of consecutive identical MediaPipe readings required to confirm
    # a gesture. At ~10 fps capture this is roughly half a second of hold.
    GESTURE_HOLD = int(os.environ.get("CC_BRIDGE_GESTURE_HOLD", "5"))

    # RoverC control/telemetry dashboard port; 0 disables. Only started when
    # a rover peer (prefix containing "RC") is actually configured.
    ROVER_PORT = int(os.environ.get("CC_BRIDGE_ROVER_PORT", str(ROVER_DEFAULT_PORT)))

    def _on_loop_start(ble, loop, log, state: BuddyState):
        if DASH_PORT > 0:
            start_dashboard(ble, loop, log=log, port=DASH_PORT)
        else:
            log.info("dashboard disabled (CC_BRIDGE_DASH_PORT=0)")
        # Rover dashboard: derive the target peer prefix from the configured
        # BLE peers (the one containing "RC"); skip entirely if no rover peer.
        # Derive the rover peer prefix from either a MultiBleWriter (._peers)
        # or a single BleWriter (._device_prefix, when only Claude-RC- is
        # configured). Without the single-writer branch the dashboard +
        # event-linked dance silently no-op in single-peer deployments.
        _peers = getattr(ble, "_peers", None)
        if _peers is not None:
            rover_prefix = next((p._device_prefix for p in _peers
                                 if "RC" in p._device_prefix), None)
        else:
            _pfx = getattr(ble, "_device_prefix", "")
            rover_prefix = _pfx if "RC" in _pfx else None
        if rover_prefix:
            # Wire the event-linked dance (apply_event reads these).
            global _ROVER_BLE, _ROVER_LOOP, _ROVER_PREFIX
            _ROVER_BLE, _ROVER_LOOP, _ROVER_PREFIX = ble, loop, rover_prefix
        if ROVER_PORT > 0 and rover_prefix:
            start_rover_dashboard(state, ble, loop, log=log,
                                  target_prefix=rover_prefix, port=ROVER_PORT)
        elif ROVER_PORT > 0:
            log.info("rover dashboard skipped (no RC peer in BLE prefixes)")
        if FRAME_PORT > 0:
            _start_frame_server(ble, loop, log, state)
        else:
            log.info("frame_server disabled (CC_BRIDGE_FRAME_PORT=0)")

    def _start_frame_server(ble, loop, log, state: BuddyState):
        """Schedule the StackChan camera-stream listener on the running loop.

        Constructed here (not via extra_tasks) so the on_frame callback can
        close over ble + state — feeding the daemon's MediaPipe Hands
        classifier and writing the confirmed gesture back to the firmware.
        """
        from buddy_core.gesture_classifier import GestureClassifier

        # MediaPipe is optional — if it isn't installed the daemon stays
        # useful (manual approval works). The classify-from-JPEG glue is
        # imported lazily so a missing mediapipe doesn't blow up at startup.
        try:
            import mediapipe  # noqa: F401 — import probe only
            _mp_ok = True
        except Exception as e:
            _mp_ok = False
            log.warning("mediapipe unavailable (%s) — gesture stays manual", e)

        classifier = GestureClassifier(hold_frames=GESTURE_HOLD)

        def _on_frame(payload: bytes) -> None:
            if not _mp_ok:
                return  # Logged once at startup; don't spam per frame.
            gesture = _classify_jpeg(payload)
            confirmed = classifier.classify(gesture)
            if confirmed is None:
                return

            # Voice control-plane takes precedence: when a routed command is
            # staged, a thumbs-up commits it (cmux send runs off-loop) and a
            # thumbs-down cancels it. Falls through to the permission-gesture
            # path below when nothing is staged, so existing behaviour is
            # unchanged whenever the control plane isn't in use.
            if _route_stager is not None and _route_stager.peek() is not None:
                log.info("route gesture: %s", confirmed)
                if confirmed == "approve":
                    def _confirm_logged():
                        # The cmux send runs off-loop; without this the
                        # exception would land on an un-awaited future and be
                        # silently swallowed ("confirmed but nothing happened").
                        try:
                            _route_stager.confirm()
                        except Exception:
                            log.exception("route confirm (cmux send) failed")
                    loop.run_in_executor(None, _confirm_logged)
                elif confirmed == "deny":
                    _route_stager.cancel()
                return

            # Permission-gesture path — only while a prompt is pending. (The
            # firmware streams frames during its prompt window; a stale frame
            # from a crashed firmware would otherwise resolve nothing.)
            prompt = state.prompt
            if not prompt:
                return
            log.info("gesture confirmed: %s (prompt id=%s)",
                     confirmed, prompt.get("id"))
            # ble.write is an async coroutine; schedule it on the loop.
            # Firmware then emits the matching {"cmd":"permission",...}
            # which on_stick_line routes to the pending future.
            asyncio.run_coroutine_threadsafe(
                ble.write({"cmd": "gesture", "result": confirmed}),
                loop,
            )

        server = FrameServer(host="0.0.0.0", port=FRAME_PORT, on_frame=_on_frame)

        async def _run():
            try:
                await server.start()
                log.info("frame_server: listening on 0.0.0.0:%d", FRAME_PORT)
                await server.serve_forever()
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("frame_server: crashed; camera stream dormant")
            finally:
                await server.stop()

        loop.create_task(_run())

    # Real classifier — decode JPEG → MediaPipe Hands → "approve" / "deny" /
    # None. Lives in buddy_core.hand_gesture so the pure-logic landmark
    # heuristic is host-testable without mediapipe. If mediapipe + pillow
    # aren't installed in the venv, classify_jpeg returns None and logs
    # once — gesture-approve goes dormant, manual approval still works.
    from buddy_core.hand_gesture import classify_jpeg as _classify_jpeg

    # Voice control-plane: stage routed voice commands; a thumbs-up gesture
    # (handled in _on_frame above) commits them into the target cmux session.
    # CmuxClient().route runs the cmux CLI; if cmux isn't installed the stager
    # simply never fires (stage works, confirm's send no-ops with an error log).
    from control_plane.cmux_control import CmuxClient
    from control_plane.stager import RouteStager
    _route_stager = RouteStager(route_fn=CmuxClient().route)

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
        extra_tasks=[reaper_loop],
        route_stager=_route_stager,
        serial_port=TAB5_SERIAL or None,
        app="claude",
    )
