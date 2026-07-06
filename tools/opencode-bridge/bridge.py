#!/usr/bin/env python3
"""
opencode-bridge — OpenCode CLI ↔ cardputer buddy daemon.

Pushes OpenCode sessions discovered from cmux's session file to cc-bridge as
ext_sessions, so the cardputer device shows OpenCode panes alongside Claude and
Cursor sessions.

No BLE device — same push-only pattern as codex-bridge. No hook events yet
(OpenCode uses an in-process plugin SDK rather than Claude-Code-shaped hooks.json).

Run manually:
  python3 tools/opencode-bridge/bridge.py
"""

import os
import sys
import time
import json
import asyncio
import pathlib
import logging

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from buddy_core import run, BuddyState

# ─── config ────────────────────────────────────────────────────────────
SOCKET_PATH = os.environ.get("OPENCODE_BRIDGE_SOCKET", "/tmp/opencode-bridge.sock")
DEVICE_PREFIX = os.environ.get("OPENCODE_BRIDGE_DEVICE_PREFIX", "OpenCode-")
LOG_PATH = os.environ.get(
    "OPENCODE_BRIDGE_LOG",
    str(pathlib.Path.home() / "Library/Logs/opencode-bridge.log"),
)
TAB5_SERIAL = os.environ.get("OPENCODE_BRIDGE_TAB5_SERIAL", "")
PTT_KEYCODE = int(os.environ.get("OPENCODE_BRIDGE_PTT_KEYCODE", "0"))  # disabled by default
PTT_MODE = os.environ.get("OPENCODE_BRIDGE_PTT_MODE", "tap")

CC_BRIDGE_SOCKET = os.environ.get("CC_BRIDGE_SOCKET", "/tmp/cc-bridge.sock")
EXT_PUSH_INTERVAL_S = 2.0

CMUX_BIN = os.environ.get("CMUX_BIN", "cmux")
CMUX_SESSION_JSON = os.environ.get(
    "CMUX_SESSION_JSON",
    str(pathlib.Path.home() / "Library/Application Support/cmux/session-com.cmuxterm.app.json"),
)


def _cmux_opencode_panes() -> dict:
    """{session_id: {cwd, label, surface_id}} for opencode cmux panes.

    Reads cmux's session file, finds panes where terminal.agent.kind == "opencode",
    returns keyed by agent.sessionId (stable, unlike codex whose cwd was the only
    option). Also returns the surface_id for selectSession focus.
    """
    try:
        with open(CMUX_SESSION_JSON, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return {}

    panes, stack = {}, [doc]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            term = o.get("terminal") or {}
            ag = term.get("agent") or {}
            if ag.get("kind") == "opencode":
                sid = ag.get("sessionId") or ""
                if not sid:
                    continue
                cwd = ag.get("workingDirectory") or term.get("workingDirectory") or ""
                label = (
                    (o.get("customTitle") if o.get("customTitleSource") == "user" else None)
                    or (cwd.rstrip("/").split("/")[-1] if cwd else "")
                    or "opencode"
                )[:24]
                surface_id = o.get("id") or ""  # cmux pane surface UUID
                panes[sid] = {"cwd": cwd, "label": label, "surface_id": surface_id}
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return panes


def _build_opencode_sessions(panes: dict) -> list:
    """sessions[] snapshot from cmux opencode panes only (no hook state yet)."""
    out = []
    for sid, info in panes.items():
        row = {
            "sid": sid[:39],   # firmware sid[40] buffer
            "label": info["label"],
            "running": False,   # hook-free: always idle for now
            "st": "idle",
            "ws": 0,
        }
        out.append(row)
        if len(out) >= 16:
            break
    return out


# ─── tasks ──────────────────────────────────────────────────────────────

async def cmux_poll_loop(state: BuddyState, dirty: asyncio.Event):
    """Poll cmux session file every 5s for opencode panes."""
    log = logging.getLogger("opencode-bridge")
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(5)
        try:
            panes = await loop.run_in_executor(None, _cmux_opencode_panes)
            prev = getattr(state, "opencode_panes", None)
            if panes != prev:
                state.opencode_panes = panes
                dirty.set()
                if panes:
                    log.info("cmux opencode panes: %d", len(panes))
        except Exception:
            log.exception("cmux poll failed")


async def push_ext_sessions_loop(state: BuddyState, dirty: asyncio.Event):
    """Push opencode sessions to cc-bridge socket every ~2s."""
    log = logging.getLogger("opencode-bridge")
    while True:
        await asyncio.sleep(EXT_PUSH_INTERVAL_S)
        panes = getattr(state, "opencode_panes", {})
        sessions = _build_opencode_sessions(panes)
        msg = json.dumps({
            "action": "ext_sessions",
            "agent": "opencode",
            "sessions": sessions,
        }) + "\n"
        try:
            r, w = await asyncio.open_unix_connection(CC_BRIDGE_SOCKET)
            w.write(msg.encode())
            await w.drain()
            w.close()
        except Exception:
            pass  # cc-bridge not up — retry next tick


# ─── main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run(
        name="opencode-bridge",
        socket_path=SOCKET_PATH,
        log_path=LOG_PATH,
        device_prefix=DEVICE_PREFIX,
        apply_event=lambda ev, state, dirty: None,
        ptt_keycode=PTT_KEYCODE,
        ptt_mode=PTT_MODE,
        keepalive_s=2.0,
        rtc_sync_on_connect=False,
        extra_tasks=[cmux_poll_loop, push_ext_sessions_loop],
        log_fmt=lambda p: f"sessions={len(p.get('sessions', []) or [])}",
        serial_port=TAB5_SERIAL or None,
        app="opencode",
        no_ble=True,
    )
