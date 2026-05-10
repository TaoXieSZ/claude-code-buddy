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
SOCKET_PATH = os.environ.get("CC_BRIDGE_SOCKET", "/tmp/cc-bridge.sock")
DEVICE_PREFIX = os.environ.get("CC_BRIDGE_DEVICE_PREFIX", "Claude-")
LOG_PATH = os.environ.get(
    "CC_BRIDGE_LOG", str(Path.home() / "Library/Logs/cc-bridge.log")
)
NUS_SVC = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
KEEPALIVE_SEC = 10.0
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
log = logging.getLogger("cc-bridge")


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
    _sessions: dict = field(default_factory=dict)  # session_id -> {"running": bool, ...}

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
    def __init__(self):
        self.client: BleakClient | None = None
        self.address: str | None = None
        self._lock = asyncio.Lock()

    async def ensure_connected(self) -> bool:
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
            log.warning("no Claude-* device in scan")
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
        log.info("connected")
        return True

    async def write(self, payload: dict):
        async with self._lock:
            if not await self.ensure_connected():
                return
            line = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
            try:
                await self.client.write_gatt_char(NUS_RX, line, response=False)
            except BleakError as e:
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


# ─── socket server + main loop ─────────────────────────────────────────
async def handle_client(reader, writer, state, ble, dirty):
    addr = writer.get_extra_info("peername") or "<peer>"
    try:
        data = await reader.read(64 * 1024)
        if not data:
            return
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


async def heartbeat_loop(state, ble, dirty):
    """Emits on dirty event OR every KEEPALIVE_SEC, whichever comes first."""
    while True:
        try:
            await asyncio.wait_for(dirty.wait(), timeout=KEEPALIVE_SEC)
        except asyncio.TimeoutError:
            pass  # keepalive
        dirty.clear()
        await ble.write(state.to_payload())


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


async def main():
    # Clean up stale socket.
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass

    state = BuddyState()
    ble = BleWriter()
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
