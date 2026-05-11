# Onboarding a second stick (and Cursor integration)

Notes for setting up another M5StickC Plus2 + BugC2 unit and wiring it
to **Cursor** (in addition to Claude Desktop / Claude Code on the first
stick). The "Future: Cursor integration" section that used to live here
is now implemented — see [`tools/cursor-bridge/`](../tools/cursor-bridge/).

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

## Cursor integration (now: `tools/cursor-bridge/`)

Cursor's hook system writes events to `~/.cursor/hooks.json` and shells
out to a script of your choice for each event. We use that — no MCP
server required.

Architecture (two-stick scenario):

```
Claude Code (terminal)         Cursor (editor)
   │ hook.py / hook_permission.py │ cursor_hook.js
   ▼                              ▼
/tmp/cc-bridge.sock         /tmp/cursor-bridge.sock
   │                              │
bridge.py (Claude-F7C2)     bridge.py (Claude-6DE2)
   │                              │
M5StickC #1                  M5StickC #2
(clawd pack)                 (calico pack)
```

Both daemons speak the same heartbeat schema (REFERENCE.md), so the
firmware is byte-identical on both sticks. cursor-bridge translates
Cursor's hook event names into the Claude Code names that
`bridge.py:apply_event()` already handles — see the table in
`tools/cursor-bridge/cursor_hook.js`.

### Onboarding stick #2 for Cursor — full sequence

Assumes you already have stick #1 paired and cc-bridge running.

1. **Flash this fork's firmware to stick #2.**
   Stock upstream firmware uses an encrypted-only NUS that bleak fights
   on macOS. This fork adds a debug-NUS service that cc-bridge /
   cursor-bridge speak.
   ```bash
   pio run -e m5stickc-plus2 -t upload --upload-port /dev/cu.usbserial-XXXX
   ```
   If you previously paired stick #2 with Claude Desktop on the
   upstream firmware, **forget the device** in System Settings →
   Bluetooth and toggle Bluetooth off/on (see §4 above for the GATT
   cache gotcha).

2. **Flash a character pack.** `calico` ships in this fork:
   ```bash
   python3 tools/flash_character.py characters/calico
   ```
   Or stick with `clawd` and visually distinguish the two sticks some
   other way (different button-press habit, sticker on the back).

3. **Pair stick #2 with macOS.** System Settings → Bluetooth, enter the
   6-digit passkey shown on the stick screen. One-time bond.

4. **Install cursor-bridge.**
   ```bash
   tools/cursor-bridge/install.sh
   ```
   Reads (and backs up) `~/.cursor/hooks.json`, merges 11 hook entries
   that point at `cursor_hook.js`. Other consumers (vibe-island, ahakey,
   omc, omr, clawd-on-desk) are left untouched.

5. **Pin both daemons** so they don't race for the same advertising
   stick. Replace `XXXX` with the last 4 hex of each stick's MAC
   (`system_profiler SPBluetoothDataType | grep Claude-` lists them):
   ```bash
   launchctl setenv CC_BRIDGE_DEVICE_PREFIX     Claude-F7C2
   launchctl setenv CURSOR_BRIDGE_DEVICE_PREFIX Claude-6DE2
   launchctl kickstart -k gui/$(id -u)/com.cc-bridge
   launchctl kickstart -k gui/$(id -u)/com.cursor-bridge
   ```

6. **Verify.** In Cursor, fire any agent action.
   ```bash
   tail -f ~/Library/Logs/cursor-bridge.log
   ```
   Within ~10s the second stick should switch from idle → "thinking…"
   → "running: shell" → "ready" as you exercise the agent.

### What v1 doesn't do

- **Stick approval gating.** cc-bridge has `hook_permission.py` that
  blocks Claude Code's PreToolUse and waits for an A/B button press.
  Cursor's permission API has a different shape (and may run inside
  the editor process rather than via shell hook); deferred to v2.
- **Per-tool granularity for MCP calls.** All MCP invocations show up
  as `mcp:<method>` rather than the full upstream tool name.

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
