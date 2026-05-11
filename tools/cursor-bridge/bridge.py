#!/usr/bin/env python3
"""
cursor-bridge — Cursor IDE ↔ M5StickC buddy daemon.

Forked from tools/cc-bridge/bridge.py with parameterized socket / log /
device-prefix names so it can run alongside cc-bridge against a separate
stick. The wire protocol, heartbeat schema, and BleWriter are identical —
the firmware needs no changes.

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

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:
    sys.exit(
        "bleak not installed. Run:\n"
        "  python3 -m pip install --user bleak\n"
        "or use the install.sh which sets up a venv."
    )

# ─── config ────────────────────────────────────────────────────────────
SOCKET_PATH = os.environ.get("CURSOR_BRIDGE_SOCKET", "/tmp/cursor-bridge.sock")
DEVICE_PREFIX = os.environ.get("CURSOR_BRIDGE_DEVICE_PREFIX", "Cursor-")
LOG_PATH = os.environ.get(
    "CURSOR_BRIDGE_LOG", str(Path.home() / "Library/Logs/cursor-bridge.log")
)
# cursor-bridge talks to the firmware's debug service (unencrypted) just
# like cc-bridge — the firmware mirrors notifies to both the encrypted
# NUS Claude Desktop uses and this debug one. This avoids the macOS
# bleak ↔ ESP32 secure-pairing instability that kept dropping the
# encrypted link mid-session.
NUS_SVC = "b0c2dbe6-cc01-4000-8000-00805f9b34fb"
NUS_RX  = "b0c2dbe6-cc02-4000-8000-00805f9b34fb"
NUS_TX  = "b0c2dbe6-cc03-4000-8000-00805f9b34fb"
KEEPALIVE_SEC = 2.0  # macOS CoreBluetooth drops idle GATT links fast; pump traffic frequently
SCAN_TIMEOUT = 8.0
RECONNECT_BACKOFF_SEC = (2, 4, 8, 16, 30)  # ramps then plateaus

# ─── logging ───────────────────────────────────────────────────────────
Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("cursor-bridge")


# ─── state model ───────────────────────────────────────────────────────
@dataclass
class BuddyState:
    """Mirrors the heartbeat JSON shape from REFERENCE.md."""
    total: int = 0
    running: int = 0
    waiting: int = 0
    msg: str = ""
    entries: list = field(default_factory=list)  # most recent first
    tokens: int = 0
    tokens_today: int = 0
    prompt: dict | None = None
    completed: bool = False  # set briefly after Stop, cleared on next emit

    # internal — not sent
    # session_id -> {"running": bool, "last_seen": float (monotonic seconds)}
    _sessions: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        p = {
            "total": self.total,
            "running": self.running,
            "waiting": self.waiting,
            "msg": self.msg,
            "entries": self.entries[:8],
            "tokens": self.tokens,
            "tokens_today": self.tokens_today,
        }
        if self.completed:
            p["completed"] = True
            self.completed = False  # one-shot
        if self.prompt is not None:
            p["prompt"] = self.prompt
        return p

    def add_entry(self, line: str):
        # Newest first, capped — matches REFERENCE.md "newest first".
        self.entries.insert(0, line[:91])
        del self.entries[8:]


# ─── hook event → state mutations ──────────────────────────────────────
def apply_event(state: BuddyState, ev: dict) -> bool:
    """Mutate state from a Claude Code hook event. Returns True if the
    payload changed materially (= we should re-emit immediately)."""
    name = ev.get("hook_event_name") or ev.get("event") or ""
    sid = ev.get("session_id") or ev.get("sessionId") or "anon"
    changed = False

    # Semantics matter — Claude Code's `Stop` is "assistant turn ended", NOT
    # "session terminated". Don't decrement `total` there. And don't set
    # `completed`; that's reserved for level-ups in the upstream protocol
    # and would otherwise fire CELEBRATE on every turn end.
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


# ─── BLE writer ────────────────────────────────────────────────────────
class BleWriter:
    def __init__(self, on_tx_line=None):
        self.client: BleakClient | None = None
        self.address: str | None = None
        self._lock = asyncio.Lock()           # serializes write_gatt_char
        self._connect_lock = asyncio.Lock()   # serializes ensure_connected
        self._on_tx_line = on_tx_line
        self._tx_buf = bytearray()

    def _tx_handler(self, _char, data: bytearray):
        # NUS TX is line-oriented JSON. Buffer until \n, then parse.
        log.info("tx raw +%d: %r", len(data), bytes(data)[:120])
        self._tx_buf.extend(data)
        while b"\n" in self._tx_buf:
            line, _, rest = bytes(self._tx_buf).partition(b"\n")
            self._tx_buf = bytearray(rest)
            line = line.strip()
            if not line:
                continue
            if self._on_tx_line:
                try:
                    self._on_tx_line(line.decode("utf-8", errors="replace"))
                except Exception as e:
                    log.warning("tx handler: %s", e)

    async def ensure_connected(self) -> bool:
        # Lock the entire connect flow — both reconnect_loop and write()
        # can call this concurrently, which used to spawn duplicate
        # BleakClient instances and cripple the NUS TX subscription
        # (the second client's start_notify would silently fight the
        # first for the same characteristic).
        async with self._connect_lock:
            if self.client and self.client.is_connected:
                return True
            log.info("scanning for stick (prefix=%s)", DEVICE_PREFIX)
            device = None
            try:
                devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
            except BleakError as e:
                log.warning("scan failed: %s", e)
                return False
            for d in devices:
                if d.name and d.name.startswith(DEVICE_PREFIX):
                    device = d
                    break
            if not device:
                log.warning("no %s* device in scan", DEVICE_PREFIX)
                return False
            log.info("connecting to %s (%s)", device.name, device.address)
            self.address = device.address
            self.client = BleakClient(device)
            try:
                await self.client.connect()
            except BleakError as e:
                log.warning("connect failed: %s", e)
                self.client = None
                return False
            # Subscribe to TX so we can receive permission acks the stick
            # sends when user presses A (decision=once) or B (decision=deny).
            try:
                await self.client.start_notify(NUS_TX, self._tx_handler)
                log.info("subscribed to NUS TX")
            except BleakError as e:
                log.warning("start_notify failed (permission echo disabled): %s", e)
            # One-shot RTC sync on connect — REFERENCE.md "one-shot on
            # connect" frame. Claude Desktop sends this, but with the
            # cursor variant there's no desktop in the loop, so the
            # stick's clock display would otherwise sit at 2000-01-01
            # until next coin-cell power. Tz offset comes from the local
            # OS so the stick shows wall time, not UTC.
            try:
                now = time.time()
                tz_offset = -time.timezone if time.daylight == 0 else -time.altzone
                line = (json.dumps({"time": [int(now), int(tz_offset)]}) + "\n").encode()
                await self.client.write_gatt_char(NUS_RX, line, response=False)
                log.info("rtc sync sent: epoch=%d tz_offset=%d", int(now), int(tz_offset))
            except (BleakError, asyncio.TimeoutError, OSError) as e:
                log.warning("rtc sync failed (non-fatal): %s", e)
            log.info("connected")
            return True

    async def write(self, payload: dict):
        async with self._lock:
            if not await self.ensure_connected():
                log.warning("write skipped: not connected")
                return
            line = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
            try:
                # response=False: write-without-response, matches Claude
                # Desktop's own use of NUS RX. With response=True the
                # bleak macOS backend would block waiting for an ack the
                # stick doesn't send for this characteristic, freezing
                # heartbeat_loop and starving subsequent emits.
                await asyncio.wait_for(
                    self.client.write_gatt_char(NUS_RX, line, response=False),
                    timeout=5.0,
                )
            except (BleakError, asyncio.TimeoutError) as e:
                log.warning("write failed (%s); dropping client", e)
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                self.client = None

    async def close(self):
        async with self._lock:
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                self.client = None


# ─── permission echo plumbing ──────────────────────────────────────────
# Pending permission requests: rid -> Future awaiting stick decision.
PENDING: dict[str, asyncio.Future] = {}


def _safe_set(fut: asyncio.Future, value):
    # macOS BLE sometimes redelivers the same notification, which would
    # call set_result twice and raise InvalidStateError on the second
    # call. The exception traceback was previously taking long enough on
    # the event loop that the awaiting wait_for raced into a timeout
    # (set_result ran but the awaiter never resumed). Idempotent set
    # avoids both: no exception, no extra loop work.
    if not fut.done():
        fut.set_result(value)


def on_stick_line(line: str):
    """Called from the BLE TX handler thread (sync). Resolve any pending
    permission future when the stick sends back an ack."""
    try:
        obj = json.loads(line)
    except Exception:
        return
    if obj.get("cmd") != "permission":
        return
    rid = obj.get("id")
    decision = obj.get("decision", "ask")
    fut = PENDING.get(rid)
    if fut and not fut.done():
        fut.get_loop().call_soon_threadsafe(_safe_set, fut, decision)


# ─── socket server + main loop ─────────────────────────────────────────
async def handle_client(reader, writer, state, ble, dirty):
    try:
        data = await reader.read(64 * 1024)
        if not data:
            return

        # First decide if this is a request/response (wait_permission) or a
        # batch of fire-and-forget hook events.
        first_line = data.splitlines()[0].strip() if data else b""
        try:
            head = json.loads(first_line) if first_line else {}
        except json.JSONDecodeError:
            head = {}

        if isinstance(head, dict) and head.get("action") == "wait_permission":
            await _handle_wait_permission(head, writer, state, dirty)
            return

        # Otherwise: treat each line as a hook event.
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                log.warning("bad event JSON: %r", line[:200])
                continue
            log.info("event: %s session=%s",
                     ev.get("hook_event_name", "?"),
                     ev.get("session_id", "?"))
            if apply_event(state, ev):
                dirty.set()
    except Exception as e:
        log.exception("handle_client: %s", e)
    finally:
        writer.close()
        await writer.wait_closed()


async def _handle_wait_permission(req, writer, state, dirty):
    rid = req.get("id") or f"req_{int(time.time() * 1000)}"
    tool = req.get("tool", "tool")
    hint = (req.get("hint") or "")[:120]
    timeout = float(req.get("timeout", 6.0))

    log.info("wait_permission id=%s tool=%s timeout=%.1fs", rid, tool, timeout)

    # Surface the prompt to the stick by setting state.prompt and
    # signalling dirty — the heartbeat loop will push it next tick.
    state.waiting = max(state.waiting, 1)
    state.prompt = {"id": rid, "tool": tool, "hint": hint}
    state.msg = f"approve: {tool}"
    dirty.set()

    fut = asyncio.get_running_loop().create_future()
    PENDING[rid] = fut
    try:
        decision = await asyncio.wait_for(fut, timeout=timeout)
        log.info("permission %s → %s", rid, decision)
    except asyncio.TimeoutError:
        decision = "ask"
        log.info("permission %s timed out → ask", rid)
    finally:
        PENDING.pop(rid, None)
        # Clear waiting state.
        state.waiting = 0
        state.prompt = None
        state.msg = ""
        dirty.set()

    try:
        writer.write((json.dumps({"decision": decision}) + "\n").encode())
        await writer.drain()
    except Exception as e:
        log.warning("reply failed: %s", e)


async def heartbeat_loop(state, ble, dirty):
    """Emits on dirty event OR every KEEPALIVE_SEC, whichever comes first."""
    while True:
        try:
            await asyncio.wait_for(dirty.wait(), timeout=KEEPALIVE_SEC)
        except asyncio.TimeoutError:
            pass  # keepalive
        dirty.clear()
        payload = state.to_payload()
        log.info("emit: running=%d waiting=%d tokens=%d/%d prompt=%s msg=%s entries[0]=%s",
                 payload.get("running", 0), payload.get("waiting", 0),
                 payload.get("tokens", 0), payload.get("tokens_today", 0),
                 (payload.get("prompt", {}) or {}).get("id", "-"),
                 payload.get("msg", "")[:40],
                 (payload.get("entries", [""])[:1] or [""])[0][:50])
        await ble.write(payload)


async def reconnect_loop(ble):
    """Background watchdog: try to keep BLE alive."""
    backoff_idx = 0
    while True:
        if ble.client and ble.client.is_connected:
            backoff_idx = 0
            await asyncio.sleep(5)
            continue
        ok = await ble.ensure_connected()
        if not ok:
            wait = RECONNECT_BACKOFF_SEC[min(backoff_idx, len(RECONNECT_BACKOFF_SEC) - 1)]
            backoff_idx += 1
            log.info("reconnect in %ss", wait)
            await asyncio.sleep(wait)


# Sessions older than this with no events are reaped on the next tick.
# Cursor IDE doesn't fire SessionEnd hooks (verified empirically), so without
# this `state.total` and `state.running` would only ever grow; a Cursor
# window that's been idle for an hour shouldn't keep counting against the
# active-session badge on the stick.
STALE_SESSION_SEC = 600  # 10 min


async def reaper_loop(state, dirty):
    """Periodically drop sessions with no recent activity, recompute counters."""
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


async def main():
    # Clean up stale socket.
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass

    state = BuddyState()
    ble = BleWriter(on_tx_line=on_stick_line)
    dirty = asyncio.Event()

    server = await asyncio.start_unix_server(
        lambda r, w: handle_client(r, w, state, ble, dirty),
        path=SOCKET_PATH,
    )
    os.chmod(SOCKET_PATH, 0o600)
    log.info("listening on %s", SOCKET_PATH)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(server.serve_forever()),
        asyncio.create_task(heartbeat_loop(state, ble, dirty)),
        asyncio.create_task(reconnect_loop(ble)),
        asyncio.create_task(reaper_loop(state, dirty)),
    ]

    await stop.wait()
    log.info("shutting down")
    for t in tasks:
        t.cancel()
    await ble.close()
    server.close()
    await server.wait_closed()
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
