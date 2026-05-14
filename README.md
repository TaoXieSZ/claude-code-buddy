# claude-code-buddy

<p align="center">
  <img src="docs/hero.png" alt="Three M5 hardware form factors — Plus2 stick on a BugC2 chassis, a bare M5Stick, and a CoreS3 StackChan — surrounded by ClaudeCode, Codex, DeepSeek, and Cursor brand marks" width="820">
</p>

Firmware + desktop daemons for **M5 hardware** that mirror the state
of an AI coding session over BLE. Two firmware targets, all driven by
the same wire protocol upstream Claude Desktop uses; this fork adds
producer daemons so the same hardware can also be driven by Claude
Code (CLI) or Cursor (IDE):

- **M5StickC Plus2** — pocketable 1.14" LCD, optional BugC2 robot
  chassis. The original target; widest producer support.
- **M5Stack StackChan (CoreS3)** — desktop pet with 2.0" display,
  two feedback servos, 12 RGB LEDs, 1W speaker. New target — *speaks*
  hook events via preloaded WAV clips, dances head/body on state
  changes, configurable from a localhost dashboard.

| Producer | Implementation | Notes |
|---|---|---|
| Claude Desktop | upstream BLE bridge (unchanged) | reference producer |
| Claude Code CLI | `tools/cc-bridge/` — launchd Python daemon + hook | supports permission echo: stick button A approves a PreToolUse prompt, B denies |
| Cursor IDE | `tools/cursor-bridge/` — launchd Python daemon + Node hook shim | mirrors prompt submission, tool start/stop, tool failures, subagent activity, token deltas. State coverage in [`tools/cursor-bridge/STATE.md`](tools/cursor-bridge/STATE.md) |

Two daemon-driven bridges can run side by side against two sticks on
one Mac. Each daemon scans by BLE name prefix (`Claude-` vs `Cursor-`)
so they don't fight over advertisements. Same firmware on both sticks;
prefix is set by build flag `BUDDY_BRAND_PREFIX`.

Hardware: M5StickC PLUS2 (1.14" 135×240 LCD, 240MHz ESP32, IMU,
buzzer). Optional BugC2 chassis (4 DC motors, 2 RGB LEDs,
STM32F030 over I2C 0x38). Software: this firmware + one Python daemon
per producer.

What's new in this fork (vs upstream `anthropics/claude-desktop-buddy`):

- `tools/cursor-bridge/` — Cursor IDE producer. Daemon, Node hook shim,
  install script, state-coverage doc.
- `tools/cc-bridge/` — Claude Code CLI producer. Includes synchronous
  PreToolUse permission echo, an unencrypted debug GATT service used to
  work around macOS+bleak BLE-encryption flakiness during back-to-back
  tool calls, and a bypass-mode short-circuit.
- `BUDDY_BRAND_PREFIX` / `BUDDY_BRAND_NAME` / `BUDDY_VARIANT_CURSOR`
  build flags. Two PlatformIO envs pin them
  (`m5stickc-plus2-claude`, `m5stickc-plus2-cursor`) along with the
  default character pack and `upload_port` / `monitor_port`.
- Stale-session reaper in cursor-bridge: sessions idle >60s drop out
  of the active-session count.
- One-shot RTC sync from the daemons on BLE reconnect. Neither the
  CLI nor the IDE producer has an upstream desktop app sending the
  periodic time frame, so without this the stick's clock would sit at
  2000-01-01.
- Clawd GIF pack (sprite art credit
  [@rullerzhou-afk/clawd-on-desk](https://github.com/rullerzhou-afk/clawd-on-desk))
  as the default character for both variants.
- M5StickC Plus2 board support via `m5_compat.h` shim over `M5Unified`.
- BugC2 chassis driver mapping the upstream persona state to motor +
  LED patterns. I2C protocol verbatim against
  `m5stack/M5Hat-BugC@c054b6e`.
- ASCII-buddy renderer (`src/buddies/*.cpp`) retired — GIF path is the
  only character renderer.

Wire protocol mostly unchanged (see [`REFERENCE.md`](REFERENCE.md)); one
small addition: stick now also sends `{"cmd":"mic","state":"down|up"}` on
PTT (press-to-talk) gesture transitions for dictation apps. Heavy thanks to
upstream for the protocol, BLE service, GIF runtime, and the seven-state
persona engine.

<p align="center">
  <img src="docs/device-plus2-bugc2.jpg" alt="M5StickC Plus2 mounted on a BugC2 chassis, screen showing the clawd buddy with mood/fed/energy stats" width="500">
</p>

## Quick start

Pick the producer you want and run that lane. Each is self-contained;
you don't need any of the others.

Prereqs (all lanes): macOS, an **M5StickC Plus2** *or* an **M5Stack
StackChan (CoreS3)**, [PlatformIO Core][pio] (`brew install platformio`
or `pipx install platformio`). StackChan-specific flash steps live
under [StackChan (CoreS3)](#stackchan-cores3) below.

[pio]: https://docs.platformio.org/en/latest/core/installation/methods/

### Lane A: Claude Desktop (official GUI app)

```bash
pio run -e m5stickc-plus2-claude -t upload -t uploadfs
```

In Claude Desktop: **Help → Troubleshooting → Enable Developer Mode**,
then **Developer → Open Hardware Buddy → Connect** and pick the stick.

### Lane B: Claude Code (terminal CLI)

```bash
pio run -e m5stickc-plus2-claude -t upload -t uploadfs
tools/cc-bridge/install.sh                          # needs jq + python3
```

Pair the stick once via macOS **System Settings → Bluetooth** (passkey
shows on the stick screen). The launchd daemon connects within ~10s.

### Lane C: Cursor IDE

```bash
pio run -e m5stickc-plus2-cursor -t upload -t uploadfs
tools/cursor-bridge/install.sh                      # needs jq + node + python3
```

Then pair the stick via macOS Bluetooth as in Lane B.

Two sticks side by side: flash one with `-claude`, one with `-cursor`,
run both bridges. They scan by name prefix (`Claude-` vs `Cursor-`) and
don't fight for advertisements. See
[`docs/onboarding-next-stick.md`](docs/onboarding-next-stick.md).

## Hardware

This fork targets two M5 platforms:

**M5StickC Plus2** (primary stick target)
- Plus and original StickC also build via the M5Unified runtime check,
  but Plus2 is the primary — see notes in `m5_compat.h`
- **BugC2 chassis** (optional) — programmable robot base, 4 DC motors,
  2 RGB LEDs, STM32F030F4P6 over I2C 0x38. Stick boots fine without
  BugC2 — the driver probes 0x38 at startup and skips silently if
  absent.

**M5Stack StackChan (CoreS3)** (new desktop pet target)
- CoreS3 head: ESP32-S3, 16 MB flash, 8 MB PSRAM, 2.0" touch LCD,
  Wi-Fi/BLE
- Body BSP: 2 feedback servos (X horizontal continuous 360°, Y vertical
  90°), 12 RGB LEDs, 1 W speaker, 3-zone capacitive touch, IR, NFC,
  INA226 power monitor
- Speaks hook events via preloaded ElevenLabs WAV clips (mirrored from
  [`shanraisshan/claude-code-hooks`](https://github.com/shanraisshan/claude-code-hooks))
- Localhost dashboard at `http://127.0.0.1:18765/` for live tuning of
  volume / brightness / character pack / motion toggles. Settings
  persist on the device via NVS.

## Flashing

Five PlatformIO envs — pick by hardware *and* by which producer:

| Env | Hardware | BLE name | Default char | Use with |
|---|---|---|---|---|
| `m5stickc-plus2-claude`  | Plus2   | `Claude-XXXX`     | `clawd`     | Claude Desktop (Lane A) or cc-bridge (Lane B) |
| `m5stickc-plus2-cursor`  | Plus2   | `Cursor-XXXX`     | `clawd`     | cursor-bridge (Lane C) |
| `m5stickc-plus2`         | Plus2   | `Claude-XXXX`     | autodetect  | legacy/plain — no baked-in defaults |
| `cores3-stackchan-claude`| CoreS3  | `Claude-SC-XXXX`  | `cloudling` | cc-bridge — see [StackChan (CoreS3)](#stackchan-cores3) |
| `cores3-stackchan-cursor`| CoreS3  | `Cursor-SC-XXXX`  | `cloudling` | cursor-bridge — see [StackChan (CoreS3)](#stackchan-cores3) |

Flash firmware **and** the LittleFS character pack in one shot:

```bash
pio run -e m5stickc-plus2-claude -t upload -t uploadfs
```

`-t upload` writes the firmware partition; `-t uploadfs` writes the
LittleFS partition (the GIF character pack). Skipping `uploadfs` on a
fresh stick boots to a no-character screen — easy to silently miss.
The `Makefile` has `make flash-claude` / `make flash-cursor` shortcuts
that always do both.

If you're starting from a previously-flashed device, wipe first:

```bash
pio run -e m5stickc-plus2-claude -t erase \
  && pio run -e m5stickc-plus2-claude -t upload -t uploadfs
```

Or wipe from the device itself: **hold A → settings → reset → factory reset**.

Two sticks attached at once? See
[`docs/onboarding-next-stick.md`](docs/onboarding-next-stick.md) for
pinning each env to a specific USB port via `upload_port` in
`platformio.ini`.

## StackChan (CoreS3)

The desktop-pet target. Same wire protocol as the Plus2 stick, but with
face GIFs on a 320×240 LCD, voice clips through the 1 W speaker, and
servo dance patterns driving the two-axis head. Settings live behind a
localhost web dashboard.

### Flash (firmware + filesystem)

```bash
pio run -e cores3-stackchan-claude -t upload -t uploadfs --upload-port /dev/cu.usbmodem<NN>
```

The CoreS3 enumerates as a native USB CDC device, so the port name is
`/dev/cu.usbmodem*` (not the `usbserial-*` that the Plus2 uses).

`uploadfs` pushes `data/` to a 3.5 MB LittleFS partition. The default
PlatformIO `default_16MB.csv` reserves most of the flash for an OTA
backup partition, so the user-data side is tight — keep `data/` lean
(default ships with one character pack + 33 WAV clips, ~2 MB).

### Wire it to the cc-bridge daemon

cc-bridge scans by BLE name prefix. The default `Claude-` matches a
Plus2 stick; StackChan advertises `Claude-SC-XXXX`. Override via env
var (or comma-separate to drive both at once):

```xml
<!-- ~/Library/LaunchAgents/com.cc-bridge.plist -->
<key>CC_BRIDGE_DEVICE_PREFIX</key>
<string>Claude-SC-</string>                <!-- StackChan only -->
<!-- or -->
<string>Claude-F7C2,Claude-SC-</string>    <!-- multi-peer -->
```

Reload after editing: `launchctl unload ~/Library/LaunchAgents/com.cc-bridge.plist && launchctl load …`.

### Dashboard

Once `cc-bridge` is running, the dashboard is at:

```
http://127.0.0.1:18765/
```

Sliders for volume and screen brightness; dropdown for character pack
(populated from `data/characters/`); toggles for servo motion master
switch and idle-wiggle. Each change POSTs through the daemon over BLE
and is persisted on the device in NVS — survives reboots.

Override port via `CC_BRIDGE_DASH_PORT=<n>` in the plist; set to `0`
to disable the dashboard.

### Sound clips

WAV clips for all 27 Claude Code hook events plus the agent_* variants
live under `data/sounds/`. Voices are pre-recorded ElevenLabs cuts
from [`shanraisshan/claude-code-hooks`](https://github.com/shanraisshan/claude-code-hooks)
(MIT, "Samara X" voice). Resampled to 16 kHz mono 16-bit so the full
set fits the LittleFS partition.

Add new clips by dropping `<eventname>.wav` into `data/sounds/` and
re-running `uploadfs` — the firmware enumerates the directory at boot,
no rebuild needed.

### Notes

- USB-C bus alone is at the edge of the current budget when both
  servos run flat-out; the firmware caps move speed at 500 (out of 1000)
  for steady patterns and only briefly hits 800 on CELEBRATE.
- The cloudling character pack is the default; clawd works on CoreS3
  too if you copy `characters/clawd/` into `data/characters/` and
  rebuild with `-DBUDDY_DEFAULT_CHAR=\"clawd\"`. Calico's GIFs hit a
  green-channel rendering bug on the CoreS3 LCD (still
  unidentified) — re-encoding pending.

## Pairing

The pairing flow depends on which producer you're using.

### Claude Desktop (Lane A)

Enable **Help → Troubleshooting → Enable Developer Mode**, then
**Developer → Open Hardware Buddy…**, click **Connect**, pick the stick.
macOS will prompt for Bluetooth permission once.

<p align="center">
  <img src="docs/menu.png" alt="Developer → Open Hardware Buddy… menu item" width="420">
  <img src="docs/hardware-buddy-window.png" alt="Hardware Buddy window with Connect button and folder drop target" width="420">
</p>

### cc-bridge / cursor-bridge (Lanes B and C)

The CLI / IDE bridges use macOS's native BLE bond, not Claude Desktop's
in-app pairing. After running the install script, pair once via
**System Settings → Bluetooth → "Other devices"** — pick `Claude-XXXX`
or `Cursor-XXXX`, then enter the 6-digit passkey shown on the stick
screen.

After the bond is in place, the launchd daemon auto-connects within
~10s every time the Mac wakes or the stick reboots. Daemon logs:

- cc-bridge: `~/Library/Logs/cc-bridge.log`
- cursor-bridge: `~/Library/Logs/cursor-bridge.log`

If you get `Peer removed pairing information` errors, the macOS bond
went stale — "Forget This Device" in Bluetooth settings and re-pair.
Common after wiping the stick or aggressive flash cycles.

## Deep dives

The stick-specific reference material lives in dedicated docs so this
README stays focused on what's new in this fork.

- **[Controls](docs/controls.md)** — Plus2 button map (standard + no-B
  mode), PTT dictation gesture, per-app PTT mode picker (Typeless /
  Doubao 长按 / 免按), screen-off semantics.
- **[Character packs](docs/character-packs.md)** — built-in packs
  (clawd, calico, bufo, cloudling), `manifest.json` shape, state→GIF
  mapping previews, pack-prep pipeline, StackChan-specific scaling.
- **[BugC2 chassis](docs/bugc2.md)** — optional Plus2 robot base; per-state
  motion + LED catalog, I²C protocol notes, manual motor calibration
  via Web Bluetooth.
- **[Persona state machine](docs/states.md)** — the seven states
  (`sleep` / `idle` / `busy` / `attention` / `celebrate` / `dizzy` /
  `heart`), what triggers each, and the per-target output mapping
  (Plus2 sprite ↔ BugC2 motion ↔ StackChan dance).

## Project layout

```
src/
  main.cpp         — Plus2 firmware entry (state machine, UI screens)
  bugc2.{h,cpp}    — Plus2 BugC2 chassis driver (I²C, motion catalog)
  m5_compat.h      — Plus / Plus2 cross-board shim over M5Unified
  ble_bridge.cpp   — NUS service for the Plus2 firmware
  character.cpp    — GIF decode + render (per-state bbox aware)
  data.h           — wire protocol, JSON parse (incl. motor cmd)
  buddies/         — ASCII species (retired) + crab.cpp (Claude mascot)
  stackchan/       — CoreS3 firmware (this fork's new target)
    main.cpp         — BLE NUS peripheral + lock-free RX ring
    character_chan.cpp — float-scale GIF render → 170 px uniform height
    sound.cpp        — preload /sounds/*.wav, M5.Speaker.playWav
    motion.cpp       — servo dance patterns via StackChan-BSP
    settings.cpp     — NVS-backed runtime settings (dashboard target)
tools/
  buddy_core/      — shared daemon library (BleWriter, heartbeat loop)
  cc-bridge/       — Claude Code CLI bridge + dashboard.py HTTP server
  cursor-bridge/   — Cursor IDE bridge
  prep_character.py / flash_character.py / motor-calib.html
characters/        — source GIFs: clawd / calico / bufo / cloudling
data/              — staged LittleFS contents (gitignored, uploadfs source)
docs/              — deep-dive references (controls, packs, BugC2, states)
platformio.ini     — five build envs (3 Plus2 variants + 2 CoreS3 variants)
mac-helper/        — Swift clipboard-sync helper
```

## Setting up another stick

Got a second M5StickC? See **[docs/onboarding-next-stick.md](docs/onboarding-next-stick.md)** —
flash gotchas (USB cable, GATT cache, heap watch), per-stick port
disambiguation, and full step-by-step for running two sticks against
Claude Code (`tools/cc-bridge/`) and Cursor (`tools/cursor-bridge/`) in
parallel, each with its own character pack.

## TODO

Roadmap for this fork (PRs welcome):

- [x] ~~**Claude Code CLI bridge** — desktop-side daemon that consumes
      Claude Code hooks~~ — shipped in `tools/cc-bridge/`. Includes
      synchronous PreToolUse hook that surfaces tool-approval prompts
      to the stick (press A=allow, B=deny). Note: macOS BLE encryption
      is flaky; the bridge currently disconnects+reconnects every few
      seconds during back-to-back tool calls, which can eat the user's
      approval window. Reliability work tracked separately below.
- [x] ~~**Cursor IDE bridge** — second daemon, second stick, same wire
      protocol~~ — shipped in `tools/cursor-bridge/`. Covers prompt
      submission, tool start/stop, tool failures, subagent activity
      (Cursor Multitask Mode), token accumulation per turn, stale
      session reaping, and one-shot RTC sync on connect. State model
      documented in [tools/cursor-bridge/STATE.md](tools/cursor-bridge/STATE.md).
      Permission echo is **not** wired yet — Cursor's permission UX
      lives inside the IDE, no API surface to drive from the stick.
- [ ] **Cursor permission echo** — Cursor exposes tool approval inside
      the IDE only; no hook event lets an external daemon answer
      yes/no. Investigate IDE extension or CLI flag to forward the
      decision back so the stick's A/B buttons can approve like
      cc-bridge does.
- [x] ~~**cc-bridge BLE stability** — bleak+CoreBluetooth on macOS keeps
      dropping the encrypted NUS link~~ — solved by adding an unencrypted
      debug NUS service that both bridges use instead. No more encryption
      flakiness mid-session.
- [x] ~~**BLE PTT dictation gesture**~~ — shipped. Stick sends
      `{"cmd":"mic","state":"down|up"}` on a tap-then-hold-A gesture
      (300ms window, 250ms hold threshold). Daemon relays this to a
      keystroke (`CC_BRIDGE_PTT_KEYCODE`, default right Option) so Typeless
      or other PTT apps pick it up. Requires `pyobjc-framework-Quartz` dep
      (added to both bridge install scripts). Stick's PDM mic dormant because
      GPIO 0 collides with BugC2 I²C SDA.
- [ ] **More GIF packs** from `clawd-on-desk` — calico, cloudling.
      `tools/prep_character.py` already supports per-state bbox so each
      pack lights up cleanly. Just write a manifest.
- [ ] **BugC2 LED-as-status overlay** — LEDs currently mirror persona
      state mood. Could double as a token-rate indicator: brightness
      proportional to recent tokens/sec.
- [ ] **Auto-calibration** for BugC2 motor asymmetry. Manual tool exists
      (`tools/motor-calib.html`); a one-shot self-test that drives a
      known pattern and uses IMU yaw drift to compute trim would be
      better than the current "click 8 buttons and eyeball straightness".
- [ ] **Land a turn-around pacing motion** — initial attempt was
      forward → 180° → forward; calibration was finicky (battery sag
      changes the 180° duration). Replaced with a simple in-place spin.
      A self-calibrating turn (use IMU yaw to close the loop) would
      revive the original idea.
- [ ] **Cliff detection** — BugC2 has no cliff sensor and the chassis
      can walk off a desk during translation. For now we accept the
      risk; an ultrasonic / IR add-on or a hard travel-distance cap
      with IMU integration would harden it.

Out of scope (researched, not feasible without hardware change):

- ~~Jumping / hopping~~ — BugC2 spec confirms no actuator beyond 4 DC
  motors + 2 RGB LEDs. The "springs" visible on the chassis are passive
  wheel suspension, not driven elements.

## Availability

The BLE API is only available when the desktop apps are in developer mode
(**Help → Troubleshooting → Enable Developer Mode**). It's intended for
makers and developers and isn't an officially supported product feature.

This fork is independently maintained — for upstream's reference protocol
docs see **[REFERENCE.md](REFERENCE.md)**.

## Credits

This fork stands on the shoulders of:

- **[`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy)**
  — the upstream firmware: state machine, BLE bridge, GIF runtime, ASCII
  sprite engine, character pack pipeline. Everything below the BugC2
  layer is upstream's design; we only added Plus2 board support, the
  crab species, and the chassis driver on top.

- **[`rullerzhou-afk/clawd-on-desk`](https://github.com/rullerzhou-afk/clawd-on-desk)**
  by [@rullerzhou-afk](https://github.com/rullerzhou-afk) — both the
  **clawd** crab (Plus2 default) and the **cloudling** cloud sprite
  (StackChan default) come from this collection. Pixel art is theirs;
  we just resize it and map each pose onto our PersonaState. If you
  like the look, go star their repo — many more poses (calico,
  building, sweeping, carrying, mini-* variants…) are easy to wire
  up by editing the pack's `manifest.json`.

- **[`shanraisshan/claude-code-hooks`](https://github.com/shanraisshan/claude-code-hooks)**
  by [@shanraisshan](https://github.com/shanraisshan) — the ElevenLabs
  "Samara X" voice clips played through the StackChan speaker. We
  mirror their `sounds/<event>/*.wav` files and resample to 16 kHz mono
  to fit LittleFS; full event-naming and triggering credit goes to
  the original hook pack.

- **[`m5stack/M5Hat-BugC`](https://github.com/m5stack/M5Hat-BugC)**
  — official BugC/BugC2 chassis library. Used as the
  ground-truth wire-protocol reference; we copied the I2C register map
  and motion patterns verbatim from `examples/bugc_robot_test/`.

- **[`m5stack/M5Unified`](https://github.com/m5stack/M5Unified)**
  — cross-board API that made M5StickC Plus2 compatibility a 1-day
  port instead of a 1-week one.
