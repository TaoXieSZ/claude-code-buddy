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
# Program flash (firmware) — pick the env matching how you'll use this stick:
pio run -e m5stickc-plus2-claude -t upload --upload-port /dev/cu.usbserial-XXXXXXX
# or:
pio run -e m5stickc-plus2-cursor -t upload --upload-port /dev/cu.usbserial-XXXXXXX
# or the plain (legacy) env without baked-in brand/character defaults:
pio run -e m5stickc-plus2        -t upload --upload-port /dev/cu.usbserial-XXXXXXX

# LittleFS partition (GIF character pack):
python3 tools/flash_character.py characters/clawd --env claude    # for -claude env
python3 tools/flash_character.py characters/clawd --env cursor    # for -cursor env

# Or, in one shot via the Makefile (firmware + LittleFS together):
make flash-claude
make flash-cursor
```

`clawd` is the default for both `-claude` and `-cursor` envs. `calico`
is also shipped in `characters/` but has a known green-background
rendering bug on Plus-1.x boards with poses wider than 135 px — stick
to `clawd` unless you're on Plus2 and accept the risk.

The two flashes are independent. After firmware flash, LittleFS is
preserved (whatever pack you had stays). After uploadfs, program is
preserved. Boot is where they meet — the firmware tries
`BUDDY_DEFAULT_CHAR` first (e.g. `clawd` for -claude), then falls back
to scanning `/characters/<name>/` if that named pack isn't installed.

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

The cleanest split is to flash each stick with a different PlatformIO
build env so they advertise under different BLE names and ship with
different default character packs:

| Stick | PlatformIO env | Advertises as | Default char pack | Bridge daemon |
|---|---|---|---|---|
| #1 (Claude Code) | `m5stickc-plus2-claude` | `Claude-XXXX` | `clawd` | cc-bridge |
| #2 (Cursor) | `m5stickc-plus2-cursor` | `Cursor-XXXX` | `clawd` | cursor-bridge |

The `m5stickc-plus2-claude` / `m5stickc-plus2-cursor` envs share source
with the plain `m5stickc-plus2` env; they only differ in two
compile-time constants (`BUDDY_BRAND_PREFIX`, `BUDDY_DEFAULT_CHAR`)
defined in `platformio.ini` build flags.

**Why two BLE names matter**: cc-bridge scans for `Claude-`,
cursor-bridge scans for `Cursor-`. Different prefixes means the two
daemons can't accidentally connect to each other's stick. No
launchctl `setenv DEVICE_PREFIX Claude-XXXX` MAC-suffix pinning
needed in this configuration.

**Backwards compatible**: a stick already flashed with the plain
`m5stickc-plus2` env still works — it advertises as `Claude-XXXX` and
scans LittleFS for any installed character. cursor-bridge can still
attach to it, but you'd then need to pin by MAC suffix as the bridges
share a prefix:

```bash
launchctl setenv CURSOR_BRIDGE_DEVICE_PREFIX Claude-6DE2
launchctl kickstart -k gui/$(id -u)/com.cursor-bridge
```

**Pairing**: each stick paired separately in macOS Bluetooth.

**One stick = Claude Code, the other = Cursor**: full sequence below.

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
   │ (scans Claude-)              │ (scans Cursor-)
bridge.py                   bridge.py
   │                              │
M5StickC #1                  M5StickC #2
firmware: m5stickc-plus2-claude   firmware: m5stickc-plus2-cursor
advertises Claude-F7C2       advertises Cursor-6DE2
clawd pack default           clawd pack default
```

Both daemons speak the same heartbeat schema (REFERENCE.md), so the
firmware is byte-identical on both sticks. cursor-bridge translates
Cursor's hook event names into the Claude Code names that
`bridge.py:apply_event()` already handles — see the table in
`tools/cursor-bridge/cursor_hook.js`.

### Onboarding stick #2 for Cursor — full sequence

Assumes you already have stick #1 paired and cc-bridge running.

1. **Flash the `-cursor` firmware variant to stick #2.**
   This builds the same source as `m5stickc-plus2`, but with two
   compile-time constants flipped: BLE advertises as `Cursor-XXXX`
   instead of `Claude-XXXX`, and the default character is pinned to
   `clawd` instead of scanning LittleFS.

   Upstream firmware uses an encrypted-only NUS that bleak fights on
   macOS — this fork adds a debug-NUS service that cc-bridge /
   cursor-bridge speak. Both -claude and -cursor envs include it.
   ```bash
   pio run -e m5stickc-plus2-cursor -t upload -t uploadfs \
     --upload-port /dev/cu.usbserial-XXXX
   ```
   The `-t uploadfs` is what actually writes the clawd character pack
   onto LittleFS — skip it and you'll boot to a no-character screen.
   `make flash-cursor` does both in one shot.

   If you previously paired stick #2 with Claude Desktop on the
   upstream firmware, **forget the device** in System Settings →
   Bluetooth and toggle Bluetooth off/on (see §4 above for the GATT
   cache gotcha). The new BLE name (`Cursor-XXXX` instead of
   `Claude-XXXX`) makes this gotcha extra likely on the first boot.

2. (Skipped — `-t uploadfs` in step 1 already wrote the clawd pack.
   Only re-run `python3 tools/flash_character.py characters/<name>
   --env cursor` if you want a different pack later.)

3. **Pair stick #2 with macOS.** System Settings → Bluetooth, enter the
   6-digit passkey shown on the stick screen. One-time bond. The stick
   should appear as `Cursor-XXXX`.

4. **Install cursor-bridge.**
   ```bash
   tools/cursor-bridge/install.sh
   ```
   Reads (and backs up) `~/.cursor/hooks.json`, merges 11 hook entries
   that point at `cursor_hook.js`. Other consumers (vibe-island, ahakey,
   omc, omr, clawd-on-desk) are left untouched. Daemon comes up
   scanning for `Cursor-` by default — no MAC-suffix pinning needed
   when each stick has its own brand prefix.

5. **Verify.** In Cursor, fire any agent action.
   ```bash
   tail -f ~/Library/Logs/cursor-bridge.log
   ```
   Within ~10s the second stick should switch from idle → "thinking…"
   → "running: shell" → "ready" as you exercise the agent. cc-bridge
   on stick #1 keeps working in parallel — the two sticks scan
   different prefixes so they never compete for the same device.

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
