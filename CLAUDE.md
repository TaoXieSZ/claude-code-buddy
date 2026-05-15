# claude-desktop-buddy — project context for future sessions

Fork of `anthropics/claude-desktop-buddy` maintained at
`TaoXieSZ/claude-code-buddy`. Upstream is dormant (last commit 2026-04-16,
2 commits total). We track our own complete branch — local `main` →
`origin/main` only, the `upstream` remote was removed on purpose.

## Repo layout (the parts that get touched a lot)

```
src/
  main.cpp              # Plus2 firmware: 1500+ LOC, button FSM, HUD, state machine
  data.h                # JSON heartbeat parse (lineGen lives here)
  character.cpp         # GIF playback, peek mode
  bugc2.cpp             # BugC2 chassis I2C detection + motor control
  audio_capture.cpp     # PDM mic infra — dormant, GPIO 0 conflicts with BugC2 SDA
  audio_ble.cpp         # ADPCM frame stream — dormant
  stackchan/            # CoreS3 firmware — separate target, own main()
    main.cpp            # BLE NUS peripheral + lock-free RX ring
    character_chan.cpp  # bilinear GIF render + ACNH-style status panel
    sound.cpp           # preload /sounds/*.wav, M5.Speaker.playWav
    motion.cpp          # servo dance patterns via StackChan-BSP
    settings.cpp        # NVS-backed runtime settings (dashboard target)
tools/
  buddy_core/core.py    # ~340 LOC shared module: BleWriter, BuddyState,
                        # make_on_stick_line (permission+mic dispatch),
                        # heartbeat_loop, reconnect_loop, run() entrypoint
  cc-bridge/bridge.py   # ~170 LOC adapter: Claude Code apply_event +
                        # SAFE_TOOLS gate + 10s keepalive
  cursor-bridge/        # ~200 LOC adapter: Cursor apply_event + last_seen
   bridge.py            # tracking + token accumulation + stale reaper +
                        # 2s keepalive + RTC sync on connect
```

PlatformIO envs — two firmware targets:
- **Plus2/Plus** (compile from `src/*.cpp`): `m5stickc-plus2`,
  `m5stickc-plus2-claude`, `m5stickc-plus2-cursor`, `m5stickc-plus`.
  The `-claude`/`-cursor` variants only differ by `BUDDY_BRAND_PREFIX` /
  `BUDDY_BRAND_NAME` build flags.
- **CoreS3/StackChan** (compile from `src/stackchan/*.cpp`):
  `cores3-stackchan`, `cores3-stackchan-claude`, `cores3-stackchan-cursor`.
- `audio_selftest` (PDM mic bringup, dormant) and `native` (host-side
  Unity test runner) are non-device envs.

## Hardware

- **M5StickC Plus2** + **BugC2** chassis. Plus1 also supported by legacy env.
- **M5Stack StackChan (CoreS3)** — desktop-pet target: 2.0" LCD, two
  feedback servos, 12 RGB LEDs, 1W speaker. Speaks hook events via
  preloaded WAVs, dances on state changes, tuned from a localhost
  dashboard. Same wire protocol as the stick.
- Plus2 PDM mic: `pin_data_in=GPIO_NUM_34`, `pin_ws=GPIO_NUM_0` (M5Unified
  source confirmed). BugC2 I²C: `SDA=GPIO_NUM_0`, `SCL=GPIO_NUM_26`.
  **GPIO 0 collision is unavoidable** — can't have both BugC2 and stick
  mic. We chose to keep BugC2 and dictate via Mac's mic relayed by daemon
  keystroke (PTT mode).
- Claude stick USB serial port: `/dev/cu.usbserial-586B0297061`. Cursor
  stick: `/dev/cu.usbserial-6D5AEF1D38`.
- BLE advertising prefixes: `Claude-F7C2*` and `Cursor-*` (sticks of the
  two `-claude` and `-cursor` PIO envs).

## Daemon architecture (post buddy_core refactor, commit ede9f48)

- Each daemon's bridge.py is a thin adapter on top of `buddy_core/core.py`.
- IDE-specific concerns kept in each bridge: `apply_event` (hook event →
  BuddyState mutations), env var names, defaults, optional reaper task.
- Shared concerns in `core.py`: BLE writer (`BleWriter`), Quartz key relay
  (`_send_key` + `_MOD_FLAGS`), socket server, heartbeat emit, reconnect.
- Daemons talk to firmware's **debug NUS service (unencrypted)**, not the
  encrypted NUS Claude Desktop uses — bleak ↔ ESP32 secure pairing was
  flaky. Firmware mirrors notifies to both services.
- **`heartbeat_loop` body wrapped in try/except** (fix 1790754) — a
  single tick failing previously killed the entire emit task silently
  (asyncio swallows exceptions on tasks not awaited).
- **`lineGen` parsing bug in src/data.h** was comparing `lines[n-1]` to
  `out->msg`; msg updates every 2s heartbeat so lineGen bumped every
  tick → `wake()` fired → idle-sleep never triggered. Fixed by stashing
  the previous newest entry and comparing to that.

## PTT dictation flow

- **Stick gesture**: short tap A (release within 300ms window), then press
  A and hold for ≥250ms. Sends `{"cmd":"mic","state":"down"}` on threshold
  cross; `{"cmd":"mic","state":"up"}` on release. Top-of-screen red REC
  banner blinks while held. Only armed from idle main (no menu, no prompt).
- **Daemon side**: `_send_key` synthesizes Quartz keystroke. Default
  keycode 61 (right Option). Modifier keys (kVK 54-63) must be emitted
  as `kCGEventFlagsChanged` events with proper `CGEventFlagMask`; non-
  modifier keys use plain keyDown/keyUp.
- **`*_BRIDGE_PTT_MODE` env var** picks app semantics:
  - `tap` (default) — single down+up per transition. Typeless toggle.
  - `hold` — keydown on mic_down, keyup on mic_up. Doubao 长按模式 +
    any classic press-to-talk app.
  - `double_tap` — double-tap on each transition. Doubao 免按模式.
- **Persisting non-default mode**: edit the EnvironmentVariables block
  in `~/Library/LaunchAgents/com.{cc,cursor}-bridge.plist` directly.
  Plist is per-machine, not in repo. README has the snippet.
- **Quartz dep**: `pyobjc-framework-Quartz` must be in both venvs
  (`~/.cc-bridge/venv`, `~/.cursor-bridge/venv`). Both install.sh scripts
  install it; manual `pip install pyobjc-framework-Quartz` for existing
  venvs.
- **Accessibility permission**: required for the venv's `python3` binary.
  First mic press triggers the system dialog.

## Stick UX cheatsheet

- BugC2 attached → no-B mode (A handles everything via tap/long-press;
  bugc2Probe at boot decides).
- 5 displayModes cycle on A short tap (in no-B mode): NORMAL → PET → INFO
  → SETTINGS-ish... See `DISP_*` enum. In INFO/PET, tap A walks pages
  until last, then falls through to next displayMode. **infoPage/petPage
  reset to 0 on that transition** (commit dc34dbf) so re-entry shows 1/N.
- HUD: bottom 68px = 8 transcript lines × 22 chars; clawd has 172px.
- Idle 15s → SLEEP gif. Screen off at 30s.
- Mic gesture (see above).

## Resource baseline

cc-bridge daemon idle: ~40 MB RSS, 0.3% CPU, 54 file descriptors. Python
daemon is not a bottleneck — Rust rewrite would only buy single-binary
distribution (worth maybe `pyinstaller` instead).

## Common dev commands

```bash
# Build + flash Claude stick
pio run -e m5stickc-plus2-claude -t upload --upload-port /dev/cu.usbserial-586B0297061

# Reload cc-bridge daemon
launchctl kickstart -k gui/$(id -u)/com.cc-bridge

# Reload after editing plist
launchctl unload ~/Library/LaunchAgents/com.cc-bridge.plist
launchctl load ~/Library/LaunchAgents/com.cc-bridge.plist

# Daemon log
tail -f ~/Library/Logs/cc-bridge.err.log
```

## Specs & tests

- **`openspec/specs/`** — source of truth for *internal behaviour*, organised by
  domain (e.g. `daemon-event-mapping`). Managed with OpenSpec (`/opsx:propose`,
  `/opsx:apply`, `/opsx:archive`; CLI: `npx @fission-ai/openspec`). Behaviour
  changes to `apply_event` / `BuddyState` / the firmware state machine go through
  an OpenSpec change, not a bare edit.
- **`REFERENCE.md`** — the *external* wire-protocol contract for forkers. Different
  audience from `openspec/specs/`; not managed by OpenSpec.
- **`.omc/specs/`** — OMC agent scratch, git-ignored. Not a spec source.
- **Tests** — `make test` runs both suites: `pytest` (Python daemons, `tests/`) and
  `pio test -e native` (pure C++ logic, Unity). `make test-py` / `make test-cpp`
  run them individually. CI (`.github/workflows/ci.yml`) runs both + the firmware
  build matrix on every PR.
- **TDD loop** — `/opsx:propose` writes a delta spec with GIVEN/WHEN/THEN scenarios →
  translate each scenario into a failing test → implement to green → `/opsx:archive`
  merges the delta into `openspec/specs/`. See `docs/development.md`.

## Working norms

- Don't push without explicit user say-so. Commit + show diff first.
- Don't auto-restart cursor-bridge — user handles their own Cursor side.
- Skip Cursor attribution in commit messages (per `~/.cursor/rules/pr-attribution.mdc`).
  Claude Co-Authored-By trailer is fine.
- Local `main` branch has the integrated history — origin/main is the
  authoritative public branch. `upstream` remote is removed.
