# Tab5 buddy — development handbook (agent handoff)

M5Stack Tab5 (ESP32-P4) as the flagship Claude/Cursor/codex desk buddy:
5″ 1280×720 touch dashboard, USB-A keyboard, mic, speaker. This doc is the
complete state of the port as of 2026-06-12 so any agent (Claude Code,
Cursor, codex) can continue without archaeology. Original research +
phased plan: `docs/proposals/tab5-buddy.md`.

## Status at a glance

| Milestone | State |
|---|---|
| P0 bringup (screen/touch/mic/battery) | ✅ 2026-06-10 |
| M0 WiFi (ESP-Hosted → C6) | ✅ 2026-06-11 (one-shot C6 firmware update required, see below) |
| M1 live feed (USB-CDC serial heartbeat) | ✅ 2026-06-11 |
| M1.5 keyboards (I2C accessory + USB-A HID) | ✅ 2026-06-11 |
| Design pass (dark dashboard, clawd GIF avatar, VLW fonts, dirty-region pushes) | ✅ 2026-06-11 |
| Hook coverage (compaction/subagents/tool failures, openspec 0005) | ✅ archived |
| Sound cues (33 WAVs over shared I2S) | ✅ 2026-06-11 |
| **M2 permission round-trip** | **🟡 allow path verified end-to-end 2026-06-12; deny untested on-device; post-timeout tap gap open (below)** |
| M3 dual live feed (Claude + Cursor) | ✅ 2026-06-13 — `app` tag routing (openspec `tab5-dashboard-ui` is the on-device side; daemon sends `app:"claude"/"cursor"`) |
| Claude-code UI redesign (role rails, mono lane, tab icons, Agent Buddy) | ✅ 2026-06-13 (openspec `tab5-dashboard-ui`) |
| Screenshot tool (agent self-verify, no camera) | ✅ 2026-06-13 (openspec `tab5-screenshot`) |
| Keyboard relay → Mac second keyboard (`cmd:key`) | ✅ 2026-06-13 (openspec `tab5-keyboard-relay`) |
| PTT dictation (`cmd:mic`) + mic audio → BlackHole | ✅ 2026-06-13 user-verified (openspec `tab5-ptt-dictation`, `tab5-mic-audio`) |
| Productize deployment (merge → launchd) | ⬜ blocked on branch merge (below) |

## Branch topology (read this first)

- **All Tab5 work lives on `feat/sticks3-buddy`** (worktree
  `.claude/worktrees/sticks3-buddy`; its upstream branch was deleted on the
  remote — local is the only copy).
- Local `main` (the main checkout, which the launchd daemon runs from) has
  **diverged**: +16 RoverC/control-plane commits (tip `8b7901a`, from Cursor
  sessions), merge-base `2dfe458`, and is also behind `origin/main`.
- Both lines touch `tools/cc-bridge/` and `tools/buddy_core/` → merging
  `feat/sticks3-buddy` into `main` needs a real conflict-aware merge.
  **Do not push anywhere without the user's explicit say-so.**

## Hardware facts

- ESP32-P4 dual-core RISC-V 400 MHz + 32 MB PSRAM; ESP32-C6 radio over
  ESP-Hosted/SDIO (`WiFi.setPins(12,13,11,10,9,8,15)` before first WiFi call).
- 5″ 1280×720 MIPI-DSI. **This unit is the 2026-04 ST7121 panel batch** —
  needs M5GFX `#develop` (driver unreleased ≤0.2.22; re-pin when 0.2.23 ships).
  Black screen + happy serial = panel driver mismatch (DSI has no readback).
  `setRotation(3)` for landscape. Warm resets intermittently fail panel init —
  setup() has a retry loop; if `display=0x0` in the boot line, reset again.
- ES7210 dual-mic + ES8388 speaker **share I2S_NUM_0** (pins MCK30 BCK27 WS29
  DIN28 DOUT26) — mutually exclusive. `src/tab5/sound.cpp` arbitrates:
  `soundPlay()` does `Mic.end()+Speaker.begin()`, `soundTick()` hands the bus
  back 300 ms after playback ends.
- USB serial: Tab5 and the sticks all enumerate as `/dev/cu.usbmodemXXXX`.
  **Always check before flashing**: `ioreg -p IOUSB -l -w0 | grep "USB Serial
  Number"` — Tab5 = `80:F1:B2:D1:51:7D`, Plus2 stick = `14:C1:9F:D4:FA:48`.
  Tab5 has been `/dev/cu.usbmodem2101` so far.
- Keyboard accessory = I2C @0x6D on ExtPort1 (SDA=GPIO0 SCL=GPIO1 INT=GPIO50,
  `Wire.begin(0,1)`); USB-A HID keyboards work via usb_host. No BLE on
  Arduino/P4 (arduino-esp32 #11788) — that's why the feed is USB-CDC serial.
- Factory C6 ships esp-hosted slave 1.4.1, incompatible with the Arduino 2.8.x
  host — run the one-shot updater `tools/tab5-c6-updater/` once per new unit.

## Firmware layout (`src/tab5/`, PIO env `m5stack-tab5`)

```
main.cpp     boot, WiFi, mic VU, battery median-of-3 (2s sampling), dim policy
ui.cpp       dashboard: sidebar/header/body, VLW fonts, dirty-region pushes,
             permission card + touch hitboxes, uiKeyEvent (Enter/y=allow,
             Esc/n=deny), uiTakeDecision queue
feed.cpp     NDJSON heartbeat parser (REFERENCE.md schema) + "play" sound
             trigger + permission verdict pump ({"cmd":"permission",...})
avatar.cpp   clawd GIF playback, 220px direct-blit fast path, 16ms frame floor
sound.cpp/.h LittleFS /sounds/*.wav preload + I2S bus arbitration
kbd.cpp      I2C accessory + USB-A HID host → uiKeyEvent
```

- `Serial.setRxBufferSize(8192)` before `begin()` — the HWCDC default 256 B
  ring silently corrupts >1 KB heartbeats.
- Partition table: `partitions_tab5_16MB.csv` (no OTA slot, app 6.25 MB,
  LittleFS 9.625 MB). `data/` holds fonts (3.9 MB) + GIFs + WAVs — **`data` is
  a symlink / untracked on purpose**: the .vlw files derive from Apple system
  fonts and must not enter the repo. Regenerate locally (below). Re-run
  uploadfs after any partition change.
- Fonts: `tools/make_vlw.py` renders PingFang SC (AssetsV2
  `com_apple_MobileAsset_Font8/86ba2c…asset/AssetData/PingFang.ttc`, idx 3
  regular / 11 semibold) + SF Pro (`/System/Library/Fonts/SFNS.ttf`,
  `--latin-variation Semibold`) into 5 .vlw faces under `data/fonts/`.

## Build / flash / verify loop

```bash
# package slot is shared with pioarduino — after building S3/Plus2 targets:
pio pkg install -e m5stack-tab5            # (and vice versa) or FRAMEWORK_DIR=None

pio run -e m5stack-tab5 -t upload --upload-port /dev/cu.usbmodem2101
pio run -e m5stack-tab5 -t uploadfs --upload-port /dev/cu.usbmodem2101   # fonts/gifs/sounds changed

# warm reset via USB-Serial-JTAG (pyserial): dtr=F,rts=F → rts=T → dtr=T,rts=F → dtr=F
# then read boot line; if "display init failed" / display=0x0 → reset again until 1280x720
```

Never pipe background `pio run` through `| tail` — it masks exit codes.
`wifi_secrets.ini` carries real hotspot credentials — **never commit it**
(skip-worktree in the main checkout; the worktree copy is newer).

## Daemon (cc-bridge) — dev runbook

The Tab5 is fed by cc-bridge over `CC_BRIDGE_TAB5_SERIAL`. The launchd
production service runs **old main-checkout code without SerialPortWriter**,
so after every Mac reboot the Tab5 goes dark until you do:

```bash
launchctl bootout gui/$(id -u)/com.cc-bridge    # kill the stale prod daemon
cd <worktree> && \
CC_BRIDGE_DEVICE_PREFIX="Claude-SC-,Claude-F7C2,Claude-RC-" \
CC_BRIDGE_PTT_MODE=hold CC_BRIDGE_SOCKET=/tmp/cc-bridge.sock \
CC_BRIDGE_TAB5_SERIAL=/dev/cu.usbmodem2101 \
CC_BRIDGE_LOG=/tmp/cc-bridge-dev.log \
~/.cc-bridge/venv/bin/python3 tools/cc-bridge/bridge.py &
grep "serial connected" /tmp/cc-bridge-dev.log
```

MUST use `~/.cc-bridge/venv/bin/python3` (bare/homebrew python3 lack bleak;
ps will misleadingly show the homebrew Framework binary — that's the venv
symlink). Flashing requires stopping whoever holds the port first.
Permanent fix = merge to main + add `CC_BRIDGE_TAB5_SERIAL` to
`~/Library/LaunchAgents/com.cc-bridge.plist` + `launchctl load`.

**Which daemon actually owns the Tab5 (2026-06-13).** In practice the Tab5
serial is held by **cursor-bridge**, running from the
`claude-desktop-buddy-cursor` checkout (`feat/cursor-next`), via the launchd
agent `com.cursor-bridge`. Its config is persisted in
`~/Library/LaunchAgents/com.cursor-bridge.plist` → `EnvironmentVariables`:

```
CURSOR_BRIDGE_TAB5_SERIAL = /dev/cu.usbmodem2101   # wired Tab5 peer
CURSOR_BRIDGE_PTT_MODE    = hold                   # 豆包 长按
CURSOR_BRIDGE_PTT_KEYCODE = 54                     # right Command
TAB5_MIC_GAIN             = 5                       # mic stream gain (×)
```

Reload after editing the plist: `launchctl bootout gui/$(id -u)/com.cursor-bridge
&& launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cursor-bridge.plist`.
The daemon-side code (serial writer, `app` routing, screenshot capture, `cmd:key`
relay, mic→BlackHole sink) lives in `buddy_core/core.py` and is **mirrored**
across the `feat/sticks3-buddy` (cc-bridge) and `feat/cursor-next`
(cursor-bridge) checkouts — change both when you touch it.

## Permission round-trip (M2) — how it works & what's left

Flow: Claude Code PreToolUse → `tools/cc-bridge/hook_permission.py`
(synchronous, registered in `~/.claude/settings.json`, timeout 10 s) →
`wait_permission` over `/tmp/cc-bridge.sock` → bridge sets `state.prompt`,
heartbeat carries it to the Tab5 → firmware draws the 权限请求 card →
user taps ✓允许/✗拒绝 (or keyboard Enter/y / Esc/n) → firmware writes
`{"cmd":"permission","id":...,"decision":"once"|"deny"}` back over serial →
bridge resolves the pending future → hook returns `permissionDecision`
allow/deny. Timeout (default 8 s, `CC_BRIDGE_PERMISSION_TIMEOUT_S`) → "ask"
= normal terminal prompt, never worse than vanilla.

- The hook **skips entirely in bypassPermissions/acceptEdits modes** (the
  tool auto-runs anyway). Testing requires default permission mode.
- StackChan peers (prefix contains "SC") can't answer; the bridge
  short-circuits to "ask" instantly when no permission-capable peer is
  connected. The Tab5 serial peer deliberately counts as capable.
- **Verified 2026-06-12**: allow path end-to-end (tap → `decision=once` →
  tool ran, no terminal prompt). Deny path is the same wire with a
  different value and is unit-tested, but not yet exercised on-device.
- **Fixed the same day**: `BleWriter.write()` used to run an 8 s BLE scan
  inline when a stick was offline, stalling every emit (serial included)
  behind it — prompts reached the Tab5 after the window had burned. write()
  now fast-skips offline peers (`reconnect_loop` owns reconnection).
  Regression test: `tests/test_buddy_core.py::test_ble_write_never_reconnects_inline`.

### Known gap: post-timeout taps are dead

After the 8 s wait expires the hook falls back to the terminal prompt, and
Claude Code's `PermissionRequest` event re-paints a card on the Tab5 (new
id). Tapping that card animates the UI (optimistic clear) but the verdict's
id matches no pending future — it is silently dropped and the session does
not react. Observed live 2026-06-12. Candidate fixes for a future agent:
1. When a verdict arrives with no pending future while a `PermissionRequest`
   prompt is active, relay a terminal keystroke (Quartz `_send_key` infra
   already exists for PTT) — answers the terminal prompt for real.
2. Have the firmware grey-out/expire the card when the prompt heartbeat
   clears instead of leaving a tappable corpse.
3. Longer `CC_BRIDGE_PERMISSION_TIMEOUT_S` (costs every unanswered tool
   call that much latency in default mode — taste carefully).

## Shipped 2026-06-13 (archived openspec changes)

Wire-protocol additions are all on the **device→daemon control channel** (same
line stream as `cmd:permission`/`cmd:mic`); none touch the heartbeat schema.
See `REFERENCE.md` for the external contract and `openspec/specs/` for the
internal specs (`tab5-dashboard-ui`, `tab5-keyboard-relay`, `tab5-ptt-dictation`,
`tab5-mic-audio`, `tab5-screenshot`).

- **Dual live feed (`app` tag).** Each daemon stamps its heartbeat with
  `app:"claude"` / `"cursor"` (`BuddyState.app`, set by `run(app=...)`).
  Firmware `feed.cpp` routes by it: `cursor`→session 1, else session 0. The
  Tab5 mic/serial is single-owner, so "two feeds at once" needs a hub
  (`docs/proposals/tab5-m3-dual-feed.md`); today whichever daemon holds the
  port shows on its correct tab.
- **Claude-code UI.** `ui.cpp`: per-role transcript rails (USER/ASSISTANT/
  TOOL/ERROR/SYSTEM, classified from `you:`/`buddy:`/`!fail` prefixes), a
  monospace lane (`F_MONO22` = SF Mono + PingFang, repurposed the unused
  `main30` slot), word-wrap + scrollbar, "Agent Buddy" wordmark, per-tab app
  icons (`logo40` Claude / `cursor_icon.c` on a light tile), avatar scaled to
  148 px (`avatarDraw/PushDirect` gained an `outSize`).
- **Screenshot tool (agent self-verify).** `{"cmd":"shot"}` → firmware streams
  the sprite as `SHOT <w> <h> <len>` + base64 + `ENDSHOT`; daemon `SerialPortWriter`
  captures it → stdlib-PNG → `/tmp/tab5-shot.png`. Trigger:
  `tools/tab5-shot/shot.py` (socket `action:"screenshot"`). **Use this instead
  of asking the user for photos.** Gotcha: the M5Canvas buffer is byte-swapped
  RGB565 — `uiScreenshot()` swaps before emit.
- **Keyboard → Mac second keyboard (`cmd:key`).** No mode toggle: the Tab5
  keyboard *always* relays (`kbd.cpp`→`feedSendKey`), dashboard is touch-driven.
  Printables → `{"ch":..}` (Unicode, layout-proof); specials/shortcuts →
  `{"key":"enter"|"a"..,"mods":[..]}`. Daemon types via Quartz (`kvk_for`,
  `_type_unicode`/`_type_keycode`).
- **PTT dictation + mic→BlackHole.** Hold the on-screen mic button → `cmd:mic`
  down/up (daemon presses the dictation hotkey; persisted as hold + right Cmd
  for 豆包) **and** the firmware streams 16 kHz mono PCM as `A<base64>` frames.
  Daemon `_BlackHoleSink` (sounddevice/PortAudio) amplifies (`TAB5_MIC_GAIN`),
  upsamples 16k→48k stereo, and plays into **BlackHole 2ch** (the dictation
  app's input). Landmines fixed: rate mismatch (16k vs 48k), and the REC
  indicator's blink forced full-frame repaints that starved the 16 kHz capture
  → made it **solid** (no per-tick repaint while held). New host dep:
  `portaudio` + `sounddevice` in the cursor-bridge venv.

## Other open work

- **True simultaneous dual-feed**: one USB serial port can't be shared by two
  daemons; the `tab5-hub` design (`docs/proposals/tab5-m3-dual-feed.md`) or a
  WiFi second transport is the path. Today: single owner per tab.
- **Volume**: speaker volume hardcoded 160/255 in `sound.cpp` — wants a
  settings module (stackchan's NVS settings.cpp is the template).
- **Tear-free rendering**: dirty-band pushes killed the visible wipe; true
  vsync would need the P4 PPA — noted, not needed so far.
- **Emit keepalive**: heartbeat keepalive only logs at DEBUG; if the Tab5
  ever looks frozen check `/tmp/cc-bridge-dev.log` for `serial` lines first.

## Process norms (inherited from the repo)

- Behaviour changes to `apply_event`/`BuddyState`/firmware state machine go
  through OpenSpec (`/opsx:propose` → tests → `/opsx:archive`), specs in
  `openspec/specs/`. Wire protocol contract for forkers: `REFERENCE.md`.
- `make test` = pytest + `pio test -e native`. Python tests need the venv:
  `~/.cc-bridge/venv/bin/python3 -m pytest tests/ -q`.
- Commit + show diff; never push without the user's explicit say-so.
