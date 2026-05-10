# Onboarding a second stick (and future editor integrations)

Notes for setting up another M5StickC Plus2 + BugC2 unit, and a placeholder
plan for adding **Cursor** alongside Claude Desktop / Claude Code.

## Flash gotchas (learned the hard way)

### 1. USB cable matters

Looks identical, behaves differently. Charge-only cables silently fail.

**Symptom**: `pio run -t upload` fails with
`Could not open /dev/cu.usbserial-... — port is busy or doesn't exist`,
even though the stick screen is on and showing a buddy.

**Check**: `ls /dev/cu.usbserial*` — if it lists nothing, your cable is
charge-only or the stick is on battery only. Swap cable or press the
side power button to wake the stick. After replug, the port re-appears
within 1-2 s.

### 2. Build command

```bash
# Program flash (firmware):
pio run -e m5stickc-plus2 -t upload --upload-port /dev/cu.usbserial-XXXXXXX

# LittleFS partition (GIF character pack):
python3 tools/flash_character.py characters/clawd
```

The two flashes are independent. After firmware flash, LittleFS is
preserved (clawd stays). After uploadfs, program is preserved. Boot is
where they meet — the firmware enumerates `/characters/<name>/`.

### 3. Per-stick port name

Each board has a different USB-UART serial (e.g.
`/dev/cu.usbserial-586B0297061`). To find yours:

```bash
ls /dev/cu.usbserial*
```

You can also let pio auto-discover: drop the `--upload-port` flag and
pio scans. Faster on a single-stick setup; explicit port is safer if
you've ever plugged multiple devices.

### 4. macOS BLE GATT cache after firmware change

If you change BLE services or characteristics in firmware, **macOS
caches the old GATT structure** and won't pick up the new layout.
bleak then reports `Characteristic ... was not found!` even though the
stick is advertising the new one.

**Fix**: System Settings → Bluetooth → click `(i)` next to the stick →
**Forget This Device**. Then toggle Bluetooth off/on from the menu bar
icon. Reconnect — services re-discovered.

### 5. Plus2 vs Plus

Plus2 has no AXP192. The stock M5StickCPlus library deadlocks on
`M5.begin()` waiting for that PMIC. **This fork uses M5Unified** with
runtime board detection (`m5_compat.h`); both Plus and Plus2 build from
the same `pio run -e m5stickc-plus2` command.

If you flash a Plus (original) board, you might want a separate env in
`platformio.ini`. Same source code, different build target.

### 6. BugC2 I2C wire

The BugC2 wants Arduino `Wire` (I2C_NUM_0), **not `Wire1`**. M5Unified's
`In_I2C` uses I2C_NUM_1 = `Wire1` for the stick's own IMU/RTC/PMIC,
which would collide. Pin pair G0/G26, 400 kHz. See
`src/bugc2.cpp` for the verbatim setup matching upstream `M5Hat-BugC`.

### 7. Heap watch

`[boot] free heap = 189840` is roughly the budget on Plus2 after
M5Unified init. Loading a clawd GIF uses ~25 KB. Audio capture
(currently disabled) used to grab another ~68 KB. You can spot
near-OOM situations in serial logs (`heap=10488` we saw a crash at
that level when attention.gif loaded). If you see crashes on state
transitions, suspect heap.

### 8. cc-bridge install per machine

Run `tools/cc-bridge/install.sh` once per Mac. It writes:
- `~/.cc-bridge/venv/` — bleak venv
- `~/Library/LaunchAgents/com.cc-bridge.plist` — daemon
- `~/.claude/settings.json` — hook entries

Idempotent on re-run. Uninstall: `tools/cc-bridge/install.sh uninstall`.

## Two sticks, one Mac

Hardware: each stick advertises as `Claude-XXXX` where XXXX is from its
MAC address (last 4 hex). They have different USB serial numbers too.

**Pairing**: each stick paired separately in macOS Bluetooth.

**cc-bridge multi-stick**: the current daemon connects to the *first*
device matching `CC_BRIDGE_DEVICE_PREFIX` (default `Claude-`). To bind
to a specific stick, set the prefix to the full name:

```bash
launchctl setenv CC_BRIDGE_DEVICE_PREFIX Claude-F7C2
launchctl kickstart -k gui/$(id -u)/com.cc-bridge
```

**Two daemons for two sticks**: clone the launchd plist with a different
label and socket path; run two instances. Out of scope for v1; do it if
you actually need both sticks live at once.

**One stick = Claude Desktop, the other = Cursor**: that's the natural
split for this fork's two-stick setup. See below.

## Future: Cursor integration

Cursor has a similar agent system to Claude Code, but its hook protocol
differs. To wire Cursor up to a stick, the daemon side mostly stays —
we just need a Cursor-side equivalent of `tools/cc-bridge/hook.py` that
emits the same heartbeat schema.

Investigation TODO before implementing:

- [ ] Does Cursor expose a hook/event API on agent lifecycle?
  Check `~/.cursor/settings.json` and the Cursor docs for "hooks",
  "PreToolUse", "Notification", or MCP server lifecycle events.
- [ ] If no hook system, fall back to **MCP server** approach — write a
  small MCP server that Cursor connects to as a tool provider. The
  server itself maintains the BLE bridge to the stick and emits state
  to it whenever Cursor calls in.
- [ ] If Cursor uses something like LangSmith / OpenTelemetry traces,
  point those at our daemon socket instead of writing a new shim.

Architecture sketch (two-stick scenario):

```
Claude Code (terminal)         Cursor (editor)
   │ hook.py                      │ cursor-hook.py (tbd)
   ▼                              ▼
/tmp/cc-bridge.sock         /tmp/cursor-bridge.sock
   │                              │
bridge.py (Claude-F7C2)     bridge.py (Claude-OTHER)
   │                              │
M5StickC #1 (lives on Mac1's  M5StickC #2 (or same Mac, two daemons,
desk) — Claude Code stick     two BLE name prefixes)
```

The bridge daemon already has the `CC_BRIDGE_DEVICE_PREFIX` knob, so
two instances with different prefixes can target two sticks. The hook
script on Cursor side is the unbuilt piece.

Before starting work: confirm Cursor actually has an addressable hook
system. If it's MCP-only, the bridge becomes an MCP server with one
tool (`buddy_notify`) that Cursor calls and the daemon turns into a
heartbeat — different shape from the cc-bridge socket protocol but the
BLE layer is unchanged.

## Useful one-liners

```bash
# tail the daemon
tail -f ~/Library/Logs/cc-bridge.log

# check if daemon is alive
launchctl list | grep cc-bridge

# manually restart daemon
launchctl kickstart -k gui/$(id -u)/com.cc-bridge

# reset settings.json hook entries (uninstall) without removing venv
tools/cc-bridge/install.sh uninstall

# read stick serial output (board reset will print boot log)
/opt/anaconda3/bin/python3 -c "
import serial, time
s = serial.Serial('/dev/cu.usbserial-XXXXXX', 115200, timeout=0.05)
s.dtr=False; s.rts=True; time.sleep(0.1); s.rts=False
end = time.time() + 10
while time.time() < end:
    n = s.in_waiting
    if n: print(s.read(n).decode(errors='replace'), end='', flush=True)
"
```
