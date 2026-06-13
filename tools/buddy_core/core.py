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

    # Source tag for multi-feed routing on a shared link (Tab5 dual-feed,
    # M3). "" omits the field (back-compatible). Set once by run(app=...).
    app: str = ""

    # Stick health snapshot from periodic {"cmd":"telemetry"} push. Not
    # part of the heartbeat payload — internal-only for dashboards. dict
    # shape: {"bat": {"pct","mV","usb"}, "imu": {"ax","ay","az"}, "ts": float}.
    stick_telemetry: dict | None = None

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
        }
        if self.app:
            p["app"] = self.app
        if self.completed:
            p["completed"] = True
            self.completed = False  # one-shot
        if self.prompt is not None:
            p["prompt"] = self.prompt
        return p

    def add_entry(self, line: str, max_len: int = 91):
        # Newest first, capped — matches REFERENCE.md "newest first". Tool/you
        # lines keep the 91-char default; the assistant answer passes a larger
        # max_len so the Tab5 (which word-wraps) shows it in full.
        self.entries.insert(0, line[:max_len])
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


# ─── keyboard relay (Tab5 as a Mac second keyboard) ────────────────────
# kVK keycodes (ANSI US layout) for named keys + letters/digits, used by the
# cmd:key relay. Printable characters are typed via Unicode (layout-proof);
# shortcuts / special keys use these keycodes + modifier flags.
_KVK_SPECIAL = {
    "enter": 0x24, "return": 0x24, "tab": 0x30, "space": 0x31,
    "backspace": 0x33, "delete": 0x33, "esc": 0x35, "escape": 0x35,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E, "fwddelete": 0x75,
}
_KVK_CHAR = {
    "a": 0x00, "b": 0x0B, "c": 0x08, "d": 0x02, "e": 0x0E, "f": 0x03, "g": 0x05,
    "h": 0x04, "i": 0x22, "j": 0x26, "k": 0x28, "l": 0x25, "m": 0x2E, "n": 0x2D,
    "o": 0x1F, "p": 0x23, "q": 0x0C, "r": 0x0F, "s": 0x01, "t": 0x11, "u": 0x20,
    "v": 0x09, "w": 0x0D, "x": 0x07, "y": 0x10, "z": 0x06,
    "0": 0x1D, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16,
    "7": 0x1A, "8": 0x1C, "9": 0x19,
}
# mod name → CGEventFlagMask
_MOD_MASK = {"cmd": 0x100000, "shift": 0x020000, "opt": 0x080000,
             "alt": 0x080000, "ctrl": 0x040000}


def kvk_for(name: str):
    """Resolve a key name to a kVK keycode (specials, then single char)."""
    name = (name or "").lower()
    kc = _KVK_SPECIAL.get(name)
    if kc is None and len(name) == 1:
        kc = _KVK_CHAR.get(name)
    return kc


def _type_unicode(ch: str) -> None:
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString,
            CGEventPost, kCGHIDEventTap,
        )
    except Exception as e:
        _log.warning("Quartz not available, key relay disabled: %s", e)
        return
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, 0, down)
        if ev is None:
            continue
        CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
        CGEventPost(kCGHIDEventTap, ev)


def _type_keycode(keycode: int, mods) -> None:
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent, CGEventSetFlags, CGEventPost, kCGHIDEventTap,
        )
    except Exception as e:
        _log.warning("Quartz not available, key relay disabled: %s", e)
        return
    flags = 0
    for m in (mods or []):
        flags |= _MOD_MASK.get(str(m).lower(), 0)
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, keycode, down)
        if ev is None:
            continue
        if flags:
            CGEventSetFlags(ev, flags)
        CGEventPost(kCGHIDEventTap, ev)


def make_on_stick_line(ptt_keycode: int, ptt_mode: str,
                       log: logging.Logger,
                       state: "BuddyState | None" = None) -> tuple[Callable[[str], None], dict]:
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

        if cmd == "telemetry":
            # Periodic stick health beat: battery + IMU. Logged + stashed
            # on BuddyState for dashboard surfacing.
            bat = obj.get("bat") or {}
            imu = obj.get("imu") or {}
            log.info("telem bat=%s%%/%smV usb=%s imu=(%s,%s,%s)",
                     bat.get("pct"), bat.get("mV"), bat.get("usb"),
                     imu.get("ax"), imu.get("ay"), imu.get("az"))
            if state is not None:
                state.stick_telemetry = {"bat": bat, "imu": imu, "ts": time.time()}
            return

        if cmd == "mic":
            mic_state = (obj.get("state") or "").lower()
            if mic_state not in ("down", "up"):
                return
            if mode == "hold":
                log.info("mic %s → key %d %s", mic_state, ptt_keycode,
                         "down" if mic_state == "down" else "up")
                _send_key(ptt_keycode, mic_state == "down")
            elif mode == "double_tap":
                log.info("mic %s → double-tap key %d", mic_state, ptt_keycode)
                _double_tap()
            else:  # tap
                log.info("mic %s → tap key %d", mic_state, ptt_keycode)
                _send_key(ptt_keycode, True)
                _send_key(ptt_keycode, False)
            return

        if cmd == "key":
            # Tab5 keyboard → Mac second keyboard. Printable chars come as
            # {"ch":"x"} (typed via Unicode, layout-proof); special keys and
            # shortcuts come as {"key":"<name>","mods":[...]} (kVK + flags).
            name = obj.get("key")
            ch = obj.get("ch")
            mods = obj.get("mods") or []
            if name:
                kc = kvk_for(name)
                if kc is not None:
                    log.info("key %s mods=%s → kVK 0x%02x", name, mods, kc)
                    _type_keycode(kc, mods)
                else:
                    log.warning("key relay: unknown key name %r", name)
            elif isinstance(ch, str) and ch:
                log.info("key ch=%r → unicode", ch)
                _type_unicode(ch)
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
            self._log.info("scanning for stick (prefix=%s)", self._device_prefix)
            device = None
            try:
                devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
            except BleakError as e:
                self._log.warning("scan failed: %s", e)
                return False
            for d in devices:
                if d.name and d.name.startswith(self._device_prefix):
                    device = d
                    break
            if not device:
                self._log.warning("no %s* device in scan", self._device_prefix)
                return False
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
            # Never scan inline: ensure_connected() for an offline peer runs
            # a BleakScanner.discover(SCAN_TIMEOUT=8s). When composed with a
            # wired serial peer (Tab5), an absent BLE stick must not stall the
            # serial heartbeat behind its scan. reconnect_loop owns reconnect.
            if not self.any_connected:
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


# ─── screenshot capture (Tab5 framebuffer → PNG, stdlib only) ──────────
def _rgb565_to_rgb888(raw: bytes, w: int, h: int) -> bytes:
    out = bytearray(w * h * 3)
    o = 0
    n = min(len(raw) - 1, w * h * 2)
    for i in range(0, n, 2):
        v = raw[i] | (raw[i + 1] << 8)          # little-endian RGB565
        r = (v >> 11) & 0x1F
        g = (v >> 5) & 0x3F
        b = v & 0x1F
        out[o] = (r << 3) | (r >> 2)
        out[o + 1] = (g << 2) | (g >> 4)
        out[o + 2] = (b << 3) | (b >> 2)
        o += 3
    return bytes(out)


def _write_png(path: str, w: int, h: int, rgb: bytes) -> None:
    """Minimal RGB PNG writer using only the stdlib (zlib)."""
    import struct
    import zlib

    def _chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)                            # filter type 0 (None)
        raw += rgb[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit, truecolor RGB
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(_chunk(b"IEND", b""))


# ─── Tab5 mic → BlackHole audio sink (sounddevice / PortAudio) ─────────
class _BlackHoleSink:
    """Plays streamed 16 kHz mono S16 PCM into a CoreAudio device (BlackHole),
    so a dictation app whose input is that device hears the Tab5 mic. Lazily
    opens on the first frame; closes on idle. PortAudio pulls from a buffer via
    a callback so the asyncio loop never blocks on audio I/O."""

    IN_RATE = 16000          # firmware streams 16 kHz mono
    OUT_RATE = 48000         # BlackHole's native rate; Doubao reads it at 48k
    OUT_CH = 2               # …in stereo
    UP = OUT_RATE // IN_RATE  # integer upsample factor (3)

    def __init__(self, device_name: str, log: logging.Logger):
        self._device_name = device_name
        self._log = log
        self._buf = bytearray()
        self._lock = __import__("threading").Lock()
        self._stream = None
        self._max = self.OUT_RATE * self.OUT_CH * 2 * 2   # ~2 s cap (out bytes)
        self._gain = float(os.environ.get("TAB5_MIC_GAIN", "5"))  # boost quiet mic
        self._fed = 0            # input bytes this session (diagnostic)

    def _transform(self, pcm: bytes) -> bytes:
        # amplify (16 kHz mono), then upsample ×UP and duplicate to stereo so
        # the stream matches BlackHole's 48 kHz/2ch that the dictation app reads.
        import array
        a = array.array("h")
        a.frombytes(pcm)
        g = self._gain
        out = array.array("h", bytes(len(a) * self.UP * self.OUT_CH * 2))
        j = 0
        for s in a:
            if g != 1.0:
                v = int(s * g)
                s = 32767 if v > 32767 else (-32768 if v < -32768 else v)
            for _ in range(self.UP):
                out[j] = s; out[j + 1] = s
                j += 2
        return out.tobytes()

    def _device_index(self):
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and self._device_name in d["name"]:
                return i
        return None

    def _cb(self, outdata, frames, time_info, status):
        need = len(outdata)
        with self._lock:
            avail = min(need, len(self._buf))
            outdata[:avail] = bytes(self._buf[:avail])
            del self._buf[:avail]
        if avail < need:
            outdata[avail:] = b"\x00" * (need - avail)

    def start(self) -> bool:
        if self._stream is not None:
            return True
        try:
            import sounddevice as sd
            idx = self._device_index()
            if idx is None:
                self._log.warning("audio sink: device %r not found", self._device_name)
                return False
            self._stream = sd.RawOutputStream(
                samplerate=self.OUT_RATE, channels=self.OUT_CH, dtype="int16",
                device=idx, callback=self._cb, blocksize=0)
            self._stream.start()
            self._log.info("audio sink open: %s (idx %d)", self._device_name, idx)
            return True
        except Exception as e:
            self._log.warning("audio sink open failed: %s", e)
            self._stream = None
            return False

    def feed(self, pcm: bytes):
        if self._stream is None and not self.start():
            return
        self._fed += len(pcm)
        pcm = self._transform(pcm)
        with self._lock:
            self._buf += pcm
            if len(self._buf) > self._max:        # drop oldest, keep recent
                del self._buf[:len(self._buf) - self._max]

    def stop(self):
        s, self._stream = self._stream, None
        if s:
            try:
                s.stop(); s.close()
            except Exception:
                pass
        if self._fed:
            self._log.info("audio sink closed: %.2f s of input", self._fed / 2 / self.IN_RATE)
            self._fed = 0
        with self._lock:
            self._buf.clear()


class SerialPortWriter:
    """NDJSON over a USB-CDC serial port — the Tab5's default link.

    Same duck-typed surface as BleWriter (any_connected / ensure_connected /
    write / close). The port comes and goes (flashing, unplug):
    ensure_connected() reopens it, the rx task exits on error and is respawned
    on the next successful open.
    """

    def __init__(self, port: str, on_tx_line=None,
                 log: logging.Logger | None = None, baud: int = 115200):
        self._port_path = port
        self._baud = baud
        self._on_tx_line = on_tx_line
        self._log = log or _log
        self._ser = None
        self._rx_task = None
        self._tx_lock = asyncio.Lock()
        self._on_connect_cb: Callable | None = None
        # screenshot capture state (SHOT … ENDSHOT frame → PNG)
        self._shot_capturing = False
        self._shot_w = self._shot_h = self._shot_len = 0
        self._shot_b64: list[str] = []
        self._shot_path = os.environ.get("TAB5_SHOT_PATH", "/tmp/tab5-shot.png")
        self._shot_event = asyncio.Event()
        # Tab5 mic → BlackHole audio stream (A<base64> frames)
        self._sink = _BlackHoleSink(os.environ.get("TAB5_MIC_SINK", "BlackHole"), self._log)
        self._audio_last = 0.0

    @property
    def any_connected(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    async def ensure_connected(self) -> bool:
        if self.any_connected:
            return True
        try:
            import serial  # pyserial — lazy so BLE-only deployments don't need it
            self._ser = serial.Serial(self._port_path, self._baud, timeout=0)
        except Exception as e:
            self._ser = None
            self._log.debug("serial open failed (%s): %s", self._port_path, e)
            return False
        self._log.info("serial connected: %s", self._port_path)
        if self._rx_task is None or self._rx_task.done():
            self._rx_task = asyncio.create_task(self._rx_loop())
        if self._on_connect_cb:
            try:
                self._on_connect_cb()
            except Exception:
                self._log.exception("serial on_connect_cb failed")
        return True

    async def _rx_loop(self):
        buf = b""
        loop = asyncio.get_event_loop()
        while True:
            ser = self._ser
            if ser is None or not getattr(ser, "is_open", False):
                return
            try:
                chunk = await loop.run_in_executor(None, ser.read, 4096)
            except Exception as e:
                self._log.info("serial dropped: %s", e)
                try:
                    ser.close()
                except Exception:
                    pass
                if self._ser is ser:
                    self._ser = None
                return
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    # screenshot frame capture takes priority over dispatch
                    if self._shot_capturing:
                        if text == "ENDSHOT":
                            self._finish_shot()
                        elif text:
                            self._shot_b64.append(text)
                            if len(self._shot_b64) > 4000:   # runaway guard
                                self._log.warning("shot frame too long; aborting")
                                self._shot_capturing = False
                                self._shot_b64 = []
                        continue
                    if text.startswith("SHOT "):
                        try:
                            p = text.split()
                            self._shot_w, self._shot_h, self._shot_len = (
                                int(p[1]), int(p[2]), int(p[3]))
                            self._shot_b64 = []
                            self._shot_capturing = True
                        except Exception:
                            self._log.warning("bad SHOT header: %r", text)
                        continue
                    # mic audio frame (A<base64> S16LE) → BlackHole sink
                    if len(text) > 1 and text[0] == "A":
                        import base64
                        try:
                            self._sink.feed(base64.b64decode(text[1:]))
                            self._audio_last = time.monotonic()
                        except Exception:
                            pass
                        continue
                    # close the audio sink shortly after the stream stops
                    if self._audio_last and time.monotonic() - self._audio_last > 0.6:
                        self._audio_last = 0.0
                        self._sink.stop()
                    # The same port carries firmware debug prints; only JSON
                    # lines go to the dispatcher.
                    if text.startswith("{") and self._on_tx_line:
                        try:
                            self._on_tx_line(text)
                        except Exception:
                            self._log.exception("serial rx line failed")
            else:
                await asyncio.sleep(0.05)

    def _finish_shot(self):
        self._shot_capturing = False
        import base64
        b64 = "".join(self._shot_b64)
        self._shot_b64 = []
        try:
            raw = base64.b64decode(b64)
        except Exception as e:
            self._log.warning("shot b64 decode failed: %s", e)
            return
        w, h = self._shot_w, self._shot_h
        if len(raw) < w * h * 2:
            self._log.warning("shot short: got %d want %d", len(raw), w * h * 2)
            return
        try:
            _write_png(self._shot_path, w, h, _rgb565_to_rgb888(raw, w, h))
            self._log.info("shot saved: %s (%dx%d)", self._shot_path, w, h)
        except Exception:
            self._log.exception("shot png write failed")
            return
        self._shot_event.set()

    async def screenshot(self, timeout: float = 8.0):
        """Request a frame from the device and return the written PNG path."""
        self._shot_event.clear()
        await self.write({"cmd": "shot"})
        try:
            await asyncio.wait_for(self._shot_event.wait(), timeout)
        except asyncio.TimeoutError:
            return None
        return self._shot_path

    async def write(self, payload: dict):
        ser = self._ser
        if ser is None or not getattr(ser, "is_open", False):
            return
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
        async with self._tx_lock:
            try:
                await asyncio.get_event_loop().run_in_executor(None, ser.write, data)
            except Exception as e:
                self._log.info("serial write failed: %s", e)
                try:
                    ser.close()
                except Exception:
                    pass
                if self._ser is ser:
                    self._ser = None

    async def close(self):
        ser, self._ser = self._ser, None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        task, self._rx_task = self._rx_task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class CompositeWriter:
    """Fan-out across heterogeneous writers (BLE peer + serial port)."""

    def __init__(self, writers: list, log: logging.Logger | None = None):
        self._writers = writers
        self._log = log or _log
        self._on_connect_cb: Callable | None = None

    @property
    def any_connected(self) -> bool:
        return any(w.any_connected for w in self._writers)

    async def ensure_connected(self) -> bool:
        rs = await asyncio.gather(
            *(w.ensure_connected() for w in self._writers),
            return_exceptions=True,
        )
        return all((not isinstance(r, Exception)) and bool(r) for r in rs)

    async def write(self, payload: dict):
        await asyncio.gather(
            *(w.write(payload) for w in self._writers),
            return_exceptions=True,
        )

    async def screenshot(self, timeout: float = 8.0):
        for w in self._writers:
            if hasattr(w, "screenshot"):
                return await w.screenshot(timeout)
        return None

    async def close(self):
        await asyncio.gather(
            *(w.close() for w in self._writers),
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

        if isinstance(head, dict) and head.get("action") == "screenshot":
            path = None
            try:
                if hasattr(ble, "screenshot"):
                    path = await ble.screenshot(float(head.get("timeout", 8.0)))
            except Exception as e:
                log.warning("screenshot action failed: %s", e)
            resp = ({"ok": True, "path": path} if path
                    else {"ok": False, "error": "no frame"})
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
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
    """Background watchdog: try to keep the writer(s) alive.

    Works for a single BleWriter or a CompositeWriter (BLE + serial); both
    expose ``any_connected`` / ``ensure_connected``."""
    backoff_idx = 0
    while True:
        if ble.any_connected:
            backoff_idx = 0
            await asyncio.sleep(5)
            continue
        ok = await ble.ensure_connected()
        if not ok:
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
    serial_port: str | None = None,
    app: str = "",
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

    # State must exist before make_on_stick_line so the telemetry handler
    # has somewhere to stash readings for the dashboard. Previously this
    # was created inside _main(); lifted to outer scope so it's a single
    # shared instance across the BLE writer and the async event loop.
    state = BuddyState()
    state.app = app

    on_stick_line, pending = make_on_stick_line(ptt_keycode, ptt_mode, log, state=state)

    ble = BleWriter(
        device_prefix=device_prefix,
        on_tx_line=on_stick_line,
        rtc_sync_on_connect=rtc_sync_on_connect,
        log=log,
    )
    if on_connect_cb:
        ble._on_connect_cb = on_connect_cb

    # Optional wired peer (Tab5 over USB-CDC) — same heartbeat fan-out, same
    # inbound dispatcher. Composes with the BLE writer built above.
    if serial_port:
        ser_w = SerialPortWriter(serial_port, on_tx_line=on_stick_line, log=log)
        if on_connect_cb:
            ser_w._on_connect_cb = on_connect_cb
        log.info("serial peer enabled: %s", serial_port)
        ble = CompositeWriter([ble, ser_w], log=log)

    async def _main():
        # Clean up stale socket.
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass

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
        # used to start a localhost dashboard HTTP server. Called before
        # the main tasks so any HTTP client connecting at t=0 sees a
        # functioning ble writer + state.
        if on_loop_start:
            try:
                import inspect
                sig_ = inspect.signature(on_loop_start)
                if len(sig_.parameters) >= 4:
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
