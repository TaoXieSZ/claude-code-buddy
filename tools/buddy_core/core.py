"""
buddy_core/core.py — shared runtime extracted from cc-bridge and cursor-bridge.

Shared surface:
  BuddyState, BleWriter, permission-echo plumbing (_safe_set, PENDING),
  PTT key relay (_send_key, _MOD_FLAGS), on_stick_line dispatcher factory,
  socket protocol (handle_client, _handle_wait_permission),
  heartbeat_loop, reconnect_loop, run() entrypoint.

IDE-specific behaviour (apply_event, env config, extra_tasks) stays in each
bridge and is injected via run().
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:
    sys.exit(
        "bleak not installed. Run:\n"
        "  python3 -m pip install --user bleak\n"
        "or use the install.sh which sets up a venv."
    )

# ─── BLE constants ─────────────────────────────────────────────────────
NUS_SVC = "b0c2dbe6-cc01-4000-8000-00805f9b34fb"
NUS_RX  = "b0c2dbe6-cc02-4000-8000-00805f9b34fb"
NUS_TX  = "b0c2dbe6-cc03-4000-8000-00805f9b34fb"
SCAN_TIMEOUT = 8.0
RECONNECT_BACKOFF_SEC = (2, 4, 8, 16, 30)  # ramps then plateaus

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
    # One-shot sound trigger — name of a pre-loaded clip on the speaker
    # device ("notification" / "permissionrequest" / ...). Set by event
    # handlers, emitted in next heartbeat as the "play" field, then
    # cleared the same way `completed` is. Stays None on most ticks.
    pending_play: str | None = None

    # HUD metrics — populated by the cc-bridge `hud` event from Claude
    # Code's statusline stdin (see openspec change 0002). Current state,
    # not one-shot: emitted on every heartbeat.
    context_pct: int = 0   # context window used %
    model: str = ""        # active model display name
    limit_5h: int = 0      # rolling 5h rate-limit used %
    limit_7d: int = 0      # rolling 7d rate-limit used %
    session_ms: int = 0    # session elapsed time, milliseconds

    # internal — not sent
    # session_id -> {"running": bool, ...}  (bridges may add "last_seen")
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
            "context_pct": self.context_pct,
            "model": self.model,
            "limit_5h": self.limit_5h,
            "limit_7d": self.limit_7d,
            "session_ms": self.session_ms,
        }
        if self.completed:
            p["completed"] = True
            self.completed = False  # one-shot
        if self.prompt is not None:
            p["prompt"] = self.prompt
        if self.pending_play:
            p["play"] = self.pending_play
            self.pending_play = None  # one-shot — firmware plays once
        return p

    def add_entry(self, line: str):
        # Newest first, capped — matches REFERENCE.md "newest first".
        self.entries.insert(0, line[:91])
        del self.entries[8:]


# ─── permission echo plumbing ──────────────────────────────────────────
# Pending permission requests: rid -> Future awaiting stick decision.
# Each run() invocation gets its own fresh dict via make_on_stick_line().
def _safe_set(fut: asyncio.Future, value):
    # macOS BLE sometimes redelivers the same notification, which would
    # call set_result twice and raise InvalidStateError on the second
    # call. The exception traceback was previously taking long enough on
    # the event loop that the awaiting wait_for raced into a timeout
    # (set_result ran but the awaiter never resumed). Idempotent set
    # avoids both: no exception, no extra loop work.
    if not fut.done():
        fut.set_result(value)


# ─── PTT key relay ─────────────────────────────────────────────────────
# PTT key relay: stick sends {"cmd":"mic","state":"down|up"}; daemon
# simulates a press/release of the configured keycode so any PTT dictation
# app on the Mac (e.g. Typeless) picks it up.
# Default: 61 = right Option (kVK_RightOption). For a modifier-only key
# (54-63) we emit a kCGEventFlagsChanged event with the correct flag
# mask; for normal keys we emit keyDown/keyUp.
# Requires Accessibility permission for the daemon's Python interpreter.

# kVK codes for modifier keys → CGEventFlagMask.
_MOD_FLAGS = {
    54: 0x100000,  # right Cmd        → kCGEventFlagMaskCommand
    55: 0x100000,  # left Cmd
    56: 0x020000,  # left Shift       → kCGEventFlagMaskShift
    58: 0x080000,  # left Option      → kCGEventFlagMaskAlternate
    59: 0x040000,  # left Control     → kCGEventFlagMaskControl
    60: 0x020000,  # right Shift
    61: 0x080000,  # right Option
    62: 0x040000,  # right Control
    63: 0x800000,  # Fn / Function    → kCGEventFlagMaskSecondaryFn
}

# Module-level logger; bridges get their own via logging.getLogger(name).
_log = logging.getLogger("buddy_core")


def _send_key(keycode: int, down: bool) -> None:
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventSetType,
            CGEventSetFlags,
            CGEventPost,
            kCGEventFlagsChanged,
            kCGHIDEventTap,
        )
    except Exception as e:
        _log.warning("Quartz not available, mic relay disabled: %s", e)
        return
    ev = CGEventCreateKeyboardEvent(None, keycode, down)
    if ev is None:
        return
    if keycode in _MOD_FLAGS:
        # Modifier-only press: switch event type to FlagsChanged so the
        # system treats it as a modifier transition, not a regular key.
        CGEventSetType(ev, kCGEventFlagsChanged)
        CGEventSetFlags(ev, _MOD_FLAGS[keycode] if down else 0)
    CGEventPost(kCGHIDEventTap, ev)


def make_on_stick_line(ptt_keycode: int, ptt_mode: str,
                       log: logging.Logger) -> tuple[Callable[[str], None], dict]:
    """Factory: returns (on_stick_line callback, PENDING dict).

    Keeping PENDING inside the closure means each daemon gets an isolated
    future map; no cross-talk if two daemons share a process (unlikely but safe).

    ptt_mode controls how mic-state transitions are translated into
    keystrokes so different dictation apps can be driven by the same stick
    gesture:

    - "tap"        : single down+up on each mic state transition. Matches
                     Typeless's toggle-style PTT hotkey ("one tap to
                     start, one tap to stop").
    - "hold"       : keydown on mic:down, keyup on mic:up. Classic
                     press-to-talk; matches Doubao's 长按模式
                     ("按住说话，松手结束").
    - "double_tap" : double-tap on each transition. Matches Doubao's
                     免按模式 ("双击开始说话，再次双击或按任意键均可结束").
    """
    mode = (ptt_mode or "tap").lower()
    if mode not in ("tap", "hold", "double_tap"):
        log.warning("unknown PTT_MODE %r, falling back to 'tap'", ptt_mode)
        mode = "tap"
    pending: dict[str, asyncio.Future] = {}

    def _double_tap():
        _send_key(ptt_keycode, True)
        _send_key(ptt_keycode, False)
        time.sleep(0.06)  # inter-tap gap — short enough not to feel laggy,
        _send_key(ptt_keycode, True)  # long enough that the OS sees two
        _send_key(ptt_keycode, False)  # discrete presses.

    def on_stick_line(line: str) -> None:
        """Called from BLE TX handler (sync). Routes stick→daemon commands:
        permission acks (resolves PENDING futures) and mic PTT relay."""
        try:
            obj = json.loads(line)
        except Exception:
            return
        cmd = obj.get("cmd")

        if cmd == "permission":
            rid = obj.get("id")
            decision = obj.get("decision", "ask")
            fut = pending.get(rid)
            if fut and not fut.done():
                fut.get_loop().call_soon_threadsafe(_safe_set, fut, decision)
            return

        if cmd == "mic":
            state = (obj.get("state") or "").lower()
            if state not in ("down", "up"):
                return
            if mode == "hold":
                log.info("mic %s → key %d %s", state, ptt_keycode,
                         "down" if state == "down" else "up")
                _send_key(ptt_keycode, state == "down")
            elif mode == "double_tap":
                log.info("mic %s → double-tap key %d", state, ptt_keycode)
                _double_tap()
            else:  # tap
                log.info("mic %s → tap key %d", state, ptt_keycode)
                _send_key(ptt_keycode, True)
                _send_key(ptt_keycode, False)
            return

    return on_stick_line, pending


# ─── BLE writer ────────────────────────────────────────────────────────
class BleWriter:
    def __init__(self, device_prefix: str, on_tx_line=None,
                 rtc_sync_on_connect: bool = False,
                 log: logging.Logger | None = None):
        self._device_prefix = device_prefix
        self._rtc_sync_on_connect = rtc_sync_on_connect
        self._log = log or _log
        self.client: BleakClient | None = None
        self.address: str | None = None
        self._lock = asyncio.Lock()           # serializes write_gatt_char
        self._connect_lock = asyncio.Lock()   # serializes ensure_connected
        self._on_tx_line = on_tx_line
        self._tx_buf = bytearray()
        self._on_connect_cb: Callable | None = None  # called after successful connect
        # macOS BleakScanner.discover() disrupts active BLE links during
        # its scan window — if Peer A is connected and Peer B is missing,
        # every poll for B knocks A offline for a few seconds. We track
        # the wall-clock of the last "scanned but found nothing" so the
        # reconnect loop can back off hard on truly-absent peers without
        # starving present ones. 30s default — long enough to not thrash,
        # short enough that a re-plugged stick reconnects within a minute.
        self._last_failed_scan_ms: float = 0.0
        self._scan_cooldown_s: float = 30.0

    def _tx_handler(self, _char, data: bytearray):
        # NUS TX is line-oriented JSON. Buffer until \n, then parse.
        self._log.info("tx raw +%d: %r", len(data), bytes(data)[:120])
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
                    self._log.warning("tx handler: %s", e)

    async def ensure_connected(self) -> bool:
        # Lock the entire connect flow — both reconnect_loop and write()
        # can call this concurrently, which used to spawn duplicate
        # BleakClient instances and cripple the NUS TX subscription
        # (the second client's start_notify would silently fight the
        # first for the same characteristic).
        async with self._connect_lock:
            if self.client and self.client.is_connected:
                return True
            # Honor the post-miss scan cooldown so an absent peer doesn't
            # repeatedly poison live peers with its scans. See note in
            # __init__ about macOS BleakScanner connection disruption.
            now_ms = time.monotonic() * 1000.0
            if self._last_failed_scan_ms and \
                    (now_ms - self._last_failed_scan_ms) < self._scan_cooldown_s * 1000.0:
                return False
            self._log.info("scanning for stick (prefix=%s)", self._device_prefix)
            device = None
            try:
                devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
            except BleakError as e:
                self._log.warning("scan failed: %s", e)
                self._last_failed_scan_ms = time.monotonic() * 1000.0
                return False
            for d in devices:
                if d.name and d.name.startswith(self._device_prefix):
                    device = d
                    break
            if not device:
                self._log.warning("no %s* device in scan (cooldown %.0fs)",
                                  self._device_prefix, self._scan_cooldown_s)
                self._last_failed_scan_ms = time.monotonic() * 1000.0
                return False
            # Found it — clear the cooldown so future drops can rescan promptly.
            self._last_failed_scan_ms = 0.0
            self._log.info("connecting to %s (%s)", device.name, device.address)
            self.address = device.address
            self.client = BleakClient(device)
            try:
                await self.client.connect()
            except BleakError as e:
                self._log.warning("connect failed: %s", e)
                self.client = None
                return False
            # Subscribe to TX so we can receive permission acks the stick
            # sends when user presses A (decision=once) or B (decision=deny).
            try:
                await self.client.start_notify(NUS_TX, self._tx_handler)
                self._log.info("subscribed to NUS TX")
            except BleakError as e:
                self._log.warning("start_notify failed (permission echo disabled): %s", e)
            # Optional one-shot RTC sync on connect.
            # cursor-bridge enables this because there's no Claude Desktop
            # in the loop to send the time frame. cc-bridge leaves it off
            # because Claude Desktop already handles it.
            if self._rtc_sync_on_connect:
                try:
                    now = time.time()
                    tz_offset = -time.timezone if time.daylight == 0 else -time.altzone
                    rtc_line = (json.dumps({"time": [int(now), int(tz_offset)]}) + "\n").encode()
                    await self.client.write_gatt_char(NUS_RX, rtc_line, response=False)
                    self._log.info("rtc sync sent: epoch=%d tz_offset=%d", int(now), int(tz_offset))
                except (BleakError, asyncio.TimeoutError, OSError) as e:
                    self._log.warning("rtc sync failed (non-fatal): %s", e)
            if self._on_connect_cb:
                try:
                    self._on_connect_cb()
                except Exception as e:
                    self._log.warning("on_connect_cb: %s", e)
            self._log.info("connected")
            return True

    async def write(self, payload: dict):
        async with self._lock:
            if not await self.ensure_connected():
                self._log.warning("write skipped: not connected")
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
                self._log.warning("write failed (%s); dropping client", e)
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

    @property
    def any_connected(self) -> bool:
        return bool(self.client and self.client.is_connected)


# ─── Multi-peer BLE writer ─────────────────────────────────────────────
# Wraps N BleWriter peers behind the same surface heartbeat/reconnect
# loops expect. Added 2026-05-13 to let one daemon drive Plus2 stick +
# StackChan CoreS3 simultaneously (different BLE adv prefixes).
#
# Design notes:
#   - write() fans out to every peer in parallel; each peer independently
#     handles its own ensure_connected, so a missing/offline peer doesn't
#     starve the others.
#   - ensure_connected() = True iff EVERY peer is connected; reconnect_loop
#     uses this to decide between the calm 5s tick (all healthy) and the
#     backoff ladder (something still trying).
#   - any_connected = True if ≥1 peer is live — used by reconnect_loop's
#     fast-path check so we don't pointlessly rescan when at least one
#     peer is already up but another is genuinely absent.
class MultiBleWriter:
    def __init__(self, prefixes: list[str], on_tx_line=None,
                 rtc_sync_on_connect: bool = False,
                 log: logging.Logger | None = None):
        self._log = log or _log
        self._peers: list[BleWriter] = [
            BleWriter(device_prefix=p, on_tx_line=on_tx_line,
                      rtc_sync_on_connect=rtc_sync_on_connect, log=log)
            for p in prefixes
        ]
        self._on_connect_cb: Callable | None = None

    @property
    def _on_connect_cb_proxy(self):  # only here for symmetry; never read
        return None

    def _propagate_on_connect(self):
        for p in self._peers:
            p._on_connect_cb = self._on_connect_cb

    @property
    def any_connected(self) -> bool:
        return any(p.any_connected for p in self._peers)

    async def ensure_connected(self) -> bool:
        # All-peers semantics: return True only when every peer is live.
        # We *do* attempt to connect peers that are currently down each
        # call — that's how a transient missing peer rejoins. Failures on
        # individual peers are logged inside BleWriter.ensure_connected.
        results = await asyncio.gather(
            *(p.ensure_connected() for p in self._peers),
            return_exceptions=True,
        )
        ok = []
        for p, r in zip(self._peers, results):
            if isinstance(r, Exception):
                self._log.warning("peer %s ensure_connected raised: %s",
                                  p._device_prefix, r)
                ok.append(False)
            else:
                ok.append(bool(r))
        return all(ok)

    async def write(self, payload: dict):
        # Fan out — each peer's BleWriter.write does its own connect
        # guard + locking. Errors logged per-peer, never raised here so
        # heartbeat_loop never loses a tick because one peer is grumpy.
        await asyncio.gather(
            *(p.write(payload) for p in self._peers),
            return_exceptions=True,
        )

    async def close(self):
        await asyncio.gather(
            *(p.close() for p in self._peers),
            return_exceptions=True,
        )


# ─── socket protocol ───────────────────────────────────────────────────
async def handle_client(reader, writer, state: BuddyState, ble: BleWriter,
                        dirty: asyncio.Event, apply_event: Callable,
                        pending: dict, log: logging.Logger):
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
            await _handle_wait_permission(head, writer, state, dirty, pending, log)
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


async def _handle_wait_permission(req, writer, state: BuddyState,
                                  dirty: asyncio.Event, pending: dict,
                                  log: logging.Logger):
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
    pending[rid] = fut
    try:
        decision = await asyncio.wait_for(fut, timeout=timeout)
        log.info("permission %s → %s", rid, decision)
    except asyncio.TimeoutError:
        decision = "ask"
        log.info("permission %s timed out → ask", rid)
    finally:
        pending.pop(rid, None)
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


# ─── loops ─────────────────────────────────────────────────────────────
async def heartbeat_loop(state: BuddyState, ble: BleWriter,
                         dirty: asyncio.Event, keepalive_s: float,
                         log: logging.Logger,
                         log_fmt: Callable[[dict], str] | None = None):
    """Emits on dirty event OR every keepalive_s, whichever comes first.

    Logs at INFO only on real state changes — keepalive ticks are DEBUG
    so the log file doesn't grow 6 MB / few hours on an idle Mac."""
    while True:
        try:
            keepalive = False
            try:
                await asyncio.wait_for(dirty.wait(), timeout=keepalive_s)
            except asyncio.TimeoutError:
                keepalive = True
            dirty.clear()
            payload = state.to_payload()
            level = logging.DEBUG if keepalive else logging.INFO
            if log_fmt:
                log.log(level, "emit: %s", log_fmt(payload))
            else:
                log.log(level,
                        "emit: running=%d waiting=%d prompt=%s msg=%s",
                        payload.get("running", 0), payload.get("waiting", 0),
                        (payload.get("prompt", {}) or {}).get("id", "-"),
                        payload.get("msg", "")[:40])
            await ble.write(payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Don't let one bad payload (encode error, transient BLE OSError,
            # downstream task raising) kill the entire emit loop and leave the
            # daemon silently un-heartbeating. Log once and continue; the next
            # tick will retry.
            log.exception("heartbeat tick failed (continuing): %s", e)
            await asyncio.sleep(1)


async def reconnect_loop(ble, log: logging.Logger):
    """Background watchdog: try to keep BLE alive.

    Works for both single BleWriter and MultiBleWriter — both expose
    ``any_connected`` and ``ensure_connected``. Calm 5s tick when every
    peer is healthy; backoff ladder when something is still trying."""
    backoff_idx = 0
    while True:
        ok = await ble.ensure_connected()  # True iff all peers connected
        if ok:
            backoff_idx = 0
            await asyncio.sleep(5)
            continue
        wait = RECONNECT_BACKOFF_SEC[min(backoff_idx, len(RECONNECT_BACKOFF_SEC) - 1)]
        backoff_idx += 1
        log.info("reconnect in %ss", wait)
        await asyncio.sleep(wait)


# ─── entrypoint ────────────────────────────────────────────────────────
def run(
    *,
    name: str,
    socket_path: str,
    log_path: str,
    device_prefix: str,
    apply_event: Callable,
    ptt_keycode: int,
    ptt_mode: str = "tap",
    keepalive_s: float,
    rtc_sync_on_connect: bool = False,
    on_connect_cb: Callable | None = None,
    extra_tasks: list[Callable] | None = None,
    log_fmt: Callable[[dict], str] | None = None,
    on_loop_start: Callable | None = None,
) -> None:
    """Configure logging, wire everything up, and run the event loop.

    Parameters
    ----------
    name:               Logger name (e.g. "cc-bridge").
    socket_path:        Unix socket path.
    log_path:           File log destination.
    device_prefix:      BLE advertisement prefix to scan for.
    apply_event:        IDE-specific hook event → state mutation function.
    ptt_keycode:        macOS kVK code for the PTT key relay.
    ptt_mode:           "tap" (Typeless toggle, default), "hold" (Doubao
                        长按 / classic PTT), or "double_tap" (Doubao 免按).
    keepalive_s:        Heartbeat keepalive interval in seconds.
    rtc_sync_on_connect: Send {"time":[epoch,tz]} on BLE connect.
    on_connect_cb:      Optional sync callback called after BLE connects.
    extra_tasks:        Optional list of ``async def(state, dirty) -> None``
                        coroutine factories to add to the task set.
    log_fmt:            Optional callable(payload) -> str for custom emit log lines.
    """
    # ── logging ──
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(sys.stderr),
        ],
    )
    log = logging.getLogger(name)

    on_stick_line, pending = make_on_stick_line(ptt_keycode, ptt_mode, log)

    # device_prefix is comma-separated for multi-peer (Plus2 + StackChan).
    # A single token (no comma) preserves the original single-peer
    # codepath so existing single-stick deployments are unaffected.
    prefixes = [p.strip() for p in device_prefix.split(",") if p.strip()]
    if len(prefixes) > 1:
        log.info("multi-peer BLE: %s", prefixes)
        ble = MultiBleWriter(
            prefixes=prefixes,
            on_tx_line=on_stick_line,
            rtc_sync_on_connect=rtc_sync_on_connect,
            log=log,
        )
        if on_connect_cb:
            ble._on_connect_cb = on_connect_cb
            ble._propagate_on_connect()
    else:
        ble = BleWriter(
            device_prefix=prefixes[0] if prefixes else device_prefix,
            on_tx_line=on_stick_line,
            rtc_sync_on_connect=rtc_sync_on_connect,
            log=log,
        )
        if on_connect_cb:
            ble._on_connect_cb = on_connect_cb

    async def _main():
        # Clean up stale socket.
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass

        state = BuddyState()
        dirty = asyncio.Event()

        server = await asyncio.start_unix_server(
            lambda r, w: handle_client(r, w, state, ble, dirty, apply_event, pending, log),
            path=socket_path,
        )
        os.chmod(socket_path, 0o600)
        log.info("listening on %s", socket_path)

        # Graceful shutdown
        loop = asyncio.get_running_loop()

        # Optional sync init that needs both ble + the running loop —
        # used by cc-bridge to start the localhost dashboard HTTP
        # server. Called before the main tasks spin up so any HTTP
        # client connecting at t=0 sees a functioning ble writer.
        if on_loop_start:
            try:
                # Backward compat: older callbacks take (ble, loop, log).
                # The 4-arg form adds `state` so frame-ingest / dashboard
                # consumers can read live BuddyState. See openspec change
                # 0003-stackchan-camera-gestures.
                import inspect
                sig = inspect.signature(on_loop_start)
                if len(sig.parameters) >= 4:
                    on_loop_start(ble, loop, log, state)
                else:
                    on_loop_start(ble, loop, log)
            except Exception as e:
                log.warning("on_loop_start failed (non-fatal): %s", e)

        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        tasks = [
            asyncio.create_task(server.serve_forever()),
            asyncio.create_task(heartbeat_loop(state, ble, dirty, keepalive_s, log, log_fmt)),
            asyncio.create_task(reconnect_loop(ble, log)),
        ]
        if extra_tasks:
            for factory in extra_tasks:
                tasks.append(asyncio.create_task(factory(state, dirty)))

        await stop.wait()
        log.info("shutting down")
        for t in tasks:
            t.cancel()
        await ble.close()
        server.close()
        await server.wait_closed()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
