#!/usr/bin/env python3
"""
codex-bridge — OpenAI Codex CLI ↔ cardputer buddy daemon.

Third agent feed, after cc-bridge (Claude) and cursor-bridge (Cursor).
openspec change cardputer-codex-sessions.

Why this is the *simplest* of the three bridges:
  - Codex CLI's hooks are ALREADY Claude-Code-shaped. ~/.codex/hooks.json
    fires SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / Stop /
    PermissionRequest with the exact field names apply_event() reads
    (session_id, cwd, tool_name, tool_input, prompt). So unlike cursor_hook.js
    — which had to translate beforeSubmitPrompt → UserPromptSubmit etc. —
    codex_hook.js is a near-identity forwarder, and apply_event below mirrors
    cc-bridge almost verbatim.
  - Aggregation is free: cc-bridge already keys ext_sessions by agent, so
    pushing {agent:"codex", sessions:[...]} to its socket merges Codex into the
    single-BLE-owner payload with no cc-bridge change. codex-bridge owns no BLE
    device of its own (it scans Codex-* in vain).

The ONE real difference from cursor-bridge — session identity:
  cmux does NOT put a session-id on a Codex pane (its title is just "codex",
  no UUID, unlike Cursor's `cursor-<UUID>`). The only stable key shared by the
  Codex hook payload AND the cmux pane is the working directory: hook `cwd` ==
  cmux pane `requested_working_directory` (verified byte-identical). So we join
  Codex hook state to live cmux panes by **cwd**, not UUID. Known limitation:
  two Codex sessions in the same directory collide on cwd and merge into one
  row (cmux exposes nothing finer). See design.md D2.

Run manually:
  python3 tools/codex-bridge/bridge.py
Run as launchd daemon:
  see tools/codex-bridge/install.sh   (com.codex-bridge)
"""

import os
import sys
import time
import json
import asyncio
import pathlib

# Allow `from buddy_core import ...` AND `from dashboard import ...`
# when launched as a standalone script.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from buddy_core import run, BuddyState
# Dashboard is optional for codex-bridge (defaults OFF — see DASH_PORT). Unlike
# cc-bridge/cursor-bridge we ship no dashboard.py in this dir, so a standalone
# daemon launch has no `dashboard` module on sys.path; import lazily/guarded so
# startup never crashes when it's absent.
try:
    from dashboard import start_dashboard, DEFAULT_PORT as DASH_DEFAULT_PORT
except Exception:  # pragma: no cover - dashboard not shipped with codex-bridge
    start_dashboard = None
    DASH_DEFAULT_PORT = 0

# ─── config ────────────────────────────────────────────────────────────
SOCKET_PATH = os.environ.get("CODEX_BRIDGE_SOCKET", "/tmp/codex-bridge.sock")
DEVICE_PREFIX = os.environ.get("CODEX_BRIDGE_DEVICE_PREFIX", "Codex-")
LOG_PATH = os.environ.get(
    "CODEX_BRIDGE_LOG", str(pathlib.Path.home() / "Library/Logs/codex-bridge.log")
)
PTT_KEYCODE = int(os.environ.get("CODEX_BRIDGE_PTT_KEYCODE", "61"))  # right Option
PTT_MODE = os.environ.get("CODEX_BRIDGE_PTT_MODE", "tap")
TAB5_SERIAL = os.environ.get("CODEX_BRIDGE_TAB5_SERIAL", "")

# Sessions older than this with no events are reaped on the next tick.
# `codex exec` doesn't fire SessionEnd (verified empirically — same as Cursor),
# and interactive Codex may not either, so without this `state.total` only ever
# grows.
STALE_SESSION_SEC = 600  # 10 min


def _cwd_of(ev: dict) -> str:
    """The working directory carried by every Codex hook event — our join key
    against live cmux Codex panes (their `requested_working_directory`)."""
    return ev.get("cwd") or ev.get("workspace") or ""


# ─── hook event → state mutations ──────────────────────────────────────
def apply_event(state: BuddyState, ev: dict) -> bool:
    """Mutate state from a Codex hook event. Returns True if the payload changed
    materially (= we should re-emit immediately).

    Codex hook events are already Claude-Code-shaped, so this mirrors
    cc-bridge / cursor-bridge. The one addition vs. cursor is that we stash the
    event's `cwd` on the session bucket so _build_codex_sessions can join to a
    live cmux pane by directory.
    """
    name = ev.get("hook_event_name") or ev.get("event") or ""
    sid = ev.get("session_id") or ev.get("sessionId") or "anon"
    cwd = _cwd_of(ev)
    changed = False

    now = time.monotonic()

    def _touch():
        s = state._sessions.get(sid)
        if s is not None:
            s["last_seen"] = now
            if cwd:
                s["cwd"] = cwd

    if name == "SessionStart":
        # Codex DOES fire SessionStart (unlike Cursor) — use it directly.
        if sid not in state._sessions:
            state._sessions[sid] = {"running": False, "last_seen": now}
            state.total += 1
            changed = True
        if cwd:
            state._sessions[sid]["cwd"] = cwd
        state._sessions[sid]["last_seen"] = now
        state.set_session_state(sid, "idle")
        state.add_entry("session start")

    elif name == "SessionEnd":
        if state._sessions.pop(sid, None):
            state.total = max(0, state.total - 1)
            state.add_entry("session ended")
            changed = True

    elif name == "UserPromptSubmit":
        # User submitted → model about to think.
        if sid in state._sessions and not state._sessions[sid].get("running"):
            state._sessions[sid]["running"] = True
            state.running += 1
        elif sid not in state._sessions:
            # Defensive: if we somehow missed SessionStart, treat this as start.
            state._sessions[sid] = {"running": True, "last_seen": now}
            state.total += 1
            state.running += 1
        if cwd:
            state._sessions[sid]["cwd"] = cwd
        state._sessions[sid]["last_seen"] = now
        prompt = ev.get("prompt") or ev.get("user_prompt") or ""
        if prompt:
            state.add_entry(f"you: {prompt}")
        state.msg = "thinking…"
        state.set_session_state(sid, "thinking")
        changed = True

    elif name == "Stop":
        # Assistant done responding (this turn). Session still open.
        s = state._sessions.get(sid)
        if s:
            s["last_seen"] = now
            if cwd:
                s["cwd"] = cwd
            if s.get("running"):
                s["running"] = False
                state.running = max(0, state.running - 1)
                state.msg = "ready"
                state.set_session_state(sid, "idle")
                changed = True
        # Codex Stop carries last_assistant_message (no token counts in the
        # hook). Surface the reply head so the transcript scroll has content.
        txt = ev.get("last_assistant_message") or ev.get("text") or ""
        if txt:
            state.add_entry(f"buddy: {str(txt).replace(chr(10), ' ').strip()}")
            changed = True
        # Defensive: accept output_tokens if a future Codex version adds it.
        ot = ev.get("output_tokens")
        if isinstance(ot, int) and ot > 0:
            state.tokens += ot
            state.tokens_today += ot
            changed = True

    elif name == "PreToolUse":
        tool = ev.get("tool_name") or "tool"
        state.msg = f"running: {tool}"
        ti = ev.get("tool_input") or {}
        hint = ti.get("command") or ti.get("description") or ti.get("file_path") or ""
        state.add_entry(f"{tool} {hint}".strip())
        _touch()
        state.set_session_state(sid, "tool")
        changed = True

    elif name == "PostToolUse":
        tool = ev.get("tool_name") or "tool"
        if ev.get("failure"):
            err = (ev.get("error") or "").strip()
            state.msg = f"failed: {tool}"
            state.add_entry(f"!fail {tool} {err}".strip())
        else:
            state.msg = f"done: {tool}"
        _touch()
        state.set_session_state(sid, "tool")
        changed = True

    elif name in ("PermissionRequest", "Notification"):
        if name == "PermissionRequest" or "permission" in (ev.get("message") or "").lower():
            tool = ev.get("tool_name") or ev.get("tool") or "tool"
            pid = ev.get("request_id") or ev.get("id") or ev.get("tool_use_id") \
                or f"req_{int(time.time())}"
            ti = ev.get("tool_input") or {}
            hint = ti.get("command") or ti.get("description") \
                or ev.get("message") or ev.get("hint") or ""
            state.waiting = max(state.waiting, 1)
            state.prompt = {"id": pid, "tool": tool, "hint": str(hint)[:120]}
            state.msg = f"approve: {tool}"
            _touch()
            state.set_session_state(sid, "waiting")
            changed = True
        else:
            msg = ev.get("message") or ev.get("title") or ""
            if msg:
                state.msg = msg[:120]
                state.add_entry(msg)
                changed = True

    elif name == "PostCompact":
        state.add_entry("compacted")
        changed = True

    return changed


# ─── push Codex sessions to cc-bridge (single-BLE-owner aggregation) ────
# codex-bridge has no BLE device of its own; it feeds its per-session snapshot
# to cc-bridge, which owns the cardputer's BLE link and merges all agents'
# sessions[] into one payload. openspec change cardputer-codex-sessions.
CC_BRIDGE_SOCKET = os.environ.get("CC_BRIDGE_SOCKET", "/tmp/cc-bridge.sock")
EXT_PUSH_INTERVAL_S = 2.0


def _build_codex_sessions(state: BuddyState) -> list:
    """sessions[] snapshot — ONLY live cmux Codex panes (state.session_labels
    {cwd: label}, populated by cmux_codex_label_loop). Per-session st/ws are
    joined from hook-tracked _sessions by **cwd** (cmux gives Codex panes no
    session-id). A hook session whose directory has no live cmux Codex pane is
    NOT listed, so the device's Codex list == focusable cmux panes. If cmux is
    unreachable (labels empty) the Codex list is empty — by design.

    Known limitation: two Codex sessions sharing one cwd collide; we keep the
    most-recently-seen one (cmux exposes nothing finer). design.md D2.
    """
    labels = getattr(state, "session_labels", {})   # {cwd: label}
    # index hook-tracked sessions by cwd, keeping the most-recently-seen per dir
    by_cwd = {}
    for sid, s in state._sessions.items():
        cwd = s.get("cwd")
        if not cwd:
            continue
        prev = by_cwd.get(cwd)
        if prev is None or s.get("last_seen", 0) >= prev[1].get("last_seen", 0):
            by_cwd[cwd] = (sid, s)

    out = []
    for cwd, label in labels.items():
        hook = by_cwd.get(cwd)
        s = hook[1] if hook else {}
        # sid must round-trip through the firmware's char sid[40] buffer AND let
        # cc-bridge focus the pane WITHOUT any daemon-side state (the select
        # callback can't see BuddyState). So the sid IS the cwd — or its last 39
        # chars when the path is longer — and cc-bridge's focus_by_codex_cwd
        # matches a Codex pane whose requested_working_directory == sid OR ends
        # with it. Display uses `label` (the basename); sid is the focus key.
        sid = cwd if len(cwd) <= 39 else cwd[-39:]
        row = {"sid": sid, "running": bool(s.get("running")), "cwd": cwd}
        if label:
            row["label"] = label
        if s.get("st"):
            row["st"] = s["st"]
        if s.get("ws"):
            row["ws"] = s["ws"]
        out.append(row)
        if len(out) >= 16:
            break
    return out


# cmux binary — self-contained query (codex-bridge has no control_plane module
# in every deployment, so we shell out directly instead of importing CmuxClient).
CMUX_BIN = os.environ.get(
    "CMUX_BIN", "/Applications/cmux.app/Contents/Resources/bin/cmux")

# cmux's session file is the ONLY place a pane's agent KIND is exposed: the
# surface.list rpc omits it, and a running agent overwrites its pane title with
# the conversation topic — so matching "codex" in the title stops working once
# the session is live (and never worked across renamed panes). The session
# file's terminal.agent.kind is stable. Override for tests / non-default installs.
CMUX_SESSION_JSON = os.environ.get(
    "CMUX_SESSION_JSON",
    str(pathlib.Path.home()
        / "Library/Application Support/cmux/session-com.cmuxterm.app.json"))


def _cmux_agent_panes(kind: str) -> dict:
    """{cwd: label} for live cmux panes whose terminal.agent.kind == kind, read
    from cmux's session file. cwd = agent.workingDirectory (the agent PROCESS
    cwd — NOT the surface's requested_working_directory, which is the launch dir
    and can differ); this is what the agent's hooks report, so it's the right
    join key. label = the user's customTitle when set, else the cwd basename.
    Returns {} if the file is missing/unreadable. openspec cardputer-codex-sessions.
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
            if ag.get("kind") == kind:
                cwd = ag.get("workingDirectory") or term.get("workingDirectory") or ""
                if cwd:
                    label = (o.get("customTitle") if o.get("customTitleSource") == "user"
                             else None) or (cwd.rstrip("/").split("/")[-1] or cwd)
                    panes[cwd] = label[:24]
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return panes


def _cmux_codex_panes() -> dict:
    """{cwd: label} for LIVE cmux Codex panes. Primary source is cmux's session
    file (terminal.agent.kind == "codex"), reliable even after a pane retitles to
    its conversation topic or moves to another workspace. Falls back to the old
    title-based surface.list scan only when the session file can't be read.
    openspec cardputer-codex-sessions.
    """
    panes = _cmux_agent_panes("codex")
    if panes:
        return panes
    # Fallback: title-based rpc scan (catches pre-retitle panes if the session
    # file is unavailable on this install).
    import subprocess

    def _rpc(method, params):
        try:
            out = subprocess.run(
                [CMUX_BIN, "rpc", method, json.dumps(params)],
                capture_output=True, text=True, timeout=5).stdout
            return json.loads(out)
        except Exception:
            return {}

    wl = _rpc("workspace.list", {})
    for ws in (wl.get("workspaces") or wl.get("items") or []):
        wid = ws.get("id") or ""
        sl = _rpc("surface.list", {"workspace": wid})
        for s in (sl.get("surfaces") or sl.get("items") or []):
            rb = s.get("resume_binding") or {}
            if rb.get("kind") == "claude" or "cursor-" in (s.get("title") or ""):
                continue
            if "codex" not in (s.get("title") or "").lower():
                continue
            cwd = s.get("requested_working_directory") or ""
            if cwd:
                panes[cwd] = (cwd.rstrip("/").split("/")[-1] or cwd)[:24]
    return panes


async def cmux_codex_label_loop(state: BuddyState, dirty: asyncio.Event):
    """Poll cmux every 15s for LIVE Codex panes → state.session_labels
    {cwd: label}. Makes the device's Codex list match what selectSession can
    focus (and carries human labels). Missing cmux → labels stay empty.
    openspec cardputer-codex-sessions.
    """
    import logging
    log = logging.getLogger("codex-bridge")
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(15)
        try:
            labels = await loop.run_in_executor(None, _cmux_codex_panes)
            if labels != getattr(state, "session_labels", None):
                state.session_labels = labels
                dirty.set()
                log.info("cmux codex labels refreshed: %d pane(s)", len(labels))
        except Exception:
            log.exception("cmux_codex_label_loop tick failed")


async def push_ext_sessions_loop(state: BuddyState, dirty: asyncio.Event):
    """Periodically push Codex sessions to cc-bridge's socket. Best-effort:
    any failure (cc-bridge down, socket missing) is swallowed and retried."""
    import logging
    log = logging.getLogger("codex-bridge")
    if not CC_BRIDGE_SOCKET:
        log.info("ext_sessions push disabled (CC_BRIDGE_SOCKET empty)")
        return
    while True:
        await asyncio.sleep(EXT_PUSH_INTERVAL_S)
        msg = json.dumps({
            "action": "ext_sessions",
            "agent": "codex",
            "sessions": _build_codex_sessions(state),
        }) + "\n"
        try:
            r, w = await asyncio.open_unix_connection(CC_BRIDGE_SOCKET)
            w.write(msg.encode())
            await w.drain()
            w.close()
        except Exception:
            pass   # cc-bridge not up / no socket — try again next tick


# ─── stale-session reaper ──────────────────────────────────────────────
async def reaper_loop(state: BuddyState, dirty: asyncio.Event):
    """Periodically drop sessions with no recent activity, recompute counters.
    Codex doesn't reliably fire SessionEnd, so without this the counters only
    ever grow. Passed to run() via extra_tasks.
    """
    import logging
    log = logging.getLogger("codex-bridge")
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
        state.total = len(state._sessions)
        state.running = sum(1 for s in state._sessions.values() if s.get("running"))
        dirty.set()


def _codex_log_fmt(payload: dict) -> str:
    return (
        f"running={payload.get('running', 0)} waiting={payload.get('waiting', 0)} "
        f"sessions={len(payload.get('sessions', []) or [])} "
        f"prompt={(payload.get('prompt', {}) or {}).get('id', '-')} "
        f"msg={payload.get('msg', '')[:40]} "
        f"entries[0]={(payload.get('entries', [''])[:1] or [''])[0][:50]}"
    )


# Dashboard defaults OFF for codex-bridge to avoid colliding with cc-bridge /
# cursor-bridge dashboards on the same Mac. Set CODEX_BRIDGE_DASH_PORT>0 to enable.
DASH_PORT = int(os.environ.get("CODEX_BRIDGE_DASH_PORT", "0"))


def _on_loop_start(ble, loop, log, state: BuddyState):
    if DASH_PORT > 0 and start_dashboard is not None:
        start_dashboard(state, ble, loop, log=log, port=DASH_PORT)
    elif DASH_PORT > 0:
        log.info("dashboard requested but module unavailable — skipping")
    else:
        log.info("dashboard disabled (CODEX_BRIDGE_DASH_PORT=0)")


if __name__ == "__main__":
    run(
        name="codex-bridge",
        socket_path=SOCKET_PATH,
        log_path=LOG_PATH,
        device_prefix=DEVICE_PREFIX,
        apply_event=apply_event,
        ptt_keycode=PTT_KEYCODE,
        ptt_mode=PTT_MODE,
        keepalive_s=2.0,
        rtc_sync_on_connect=True,
        extra_tasks=[reaper_loop, push_ext_sessions_loop, cmux_codex_label_loop],
        log_fmt=_codex_log_fmt,
        on_loop_start=_on_loop_start,
        serial_port=TAB5_SERIAL or None,
        app="codex",
        # codex-bridge owns no stick — it pushes ext_sessions to cc-bridge (the
        # single BLE owner). Scanning for a non-existent Codex-* device would
        # contend with cc-bridge's cardputer link on the shared macOS BLE radio
        # and flap it (observed 2026-06-26). Push-only: no BLE scan.
        no_ble=True,
    )
