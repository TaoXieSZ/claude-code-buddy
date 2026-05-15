# Design

## Data path

```
StackChan (CoreS3)                          Mac daemon (buddy_core + cc-bridge)
┌───────────────────────────┐               ┌────────────────────────────────────┐
│ permission prompt pending │               │ Claude Code PermissionRequest      │
│   → camera_chan init      │               │   → apply_event → state.prompt set │
│   → wifi_stream connect   │               │   → heartbeat carries prompt       │
│                           │  TCP JPEG     │                                    │
│ capture QVGA RGB565       │ ────stream───▶ │ frame ingest server                │
│   frame2jpg(fb,80)        │               │   → MediaPipe Hands                │
│                           │               │   → thumbs-up/down + debounce      │
│                           │  BLE NUS      │                                    │
│ {"cmd":"gesture",...}     │ ◀────cmd────── │ confirmed gesture → UI feedback    │
│   → ATTENTION UI reacts   │               │                                    │
│                           │  BLE NUS      │                                    │
│ {"cmd":"permission",      │ ────debug────▶ │ → resolve Claude Code prompt       │
│   "id","decision"}        │      TX       │   (approve/deny the pending tool)  │
│                           │               │                                    │
│ prompt clears → camera +  │               │                                    │
│   wifi_stream torn down   │               │                                    │
└───────────────────────────┘               └────────────────────────────────────┘
```

The camera/WiFi lifecycle is bound to the permission-prompt window. No prompt →
no camera, no WiFi stream, no I2C bus release. This is the privacy posture and
also bounds the side effect described below.

## The I2C-bus-release constraint (load-bearing)

Verbatim upstream (`cores3-camera-upstream-reference.md`, pinned commits) shows
the GC0308 SCCB lines are GPIO11/12 — the **CoreS3 internal system I2C bus**
shared with AXP2101 PMIC, AW9523B, BM8563 RTC, BMI270 IMU, ES7210, **AW88298
speaker amp**, FT6336 touch. The working upstream port calls
`M5.In_I2C.release()` before `esp_camera_init()` so esp32-camera privately owns
GPIO11/12 (`sccb_i2c_port = -1`).

**Consequence:** while the camera is live, M5Unified cannot reach the speaker
amp, IMU, RTC, or touch over I2C. For this firmware that means:

- **`sound.cpp` cannot play WAVs while the camera is active.** Acceptable because
  the camera only runs during a permission prompt — a few seconds — and the
  ATTENTION state is not a sound-heavy moment. Any queued sound plays after
  teardown.
- **Servos (`motion.cpp`)** are LEDC PWM, **not** I2C — unaffected. The ATTENTION
  look-left-right pattern keeps running during capture.
- **LCD (`character_chan.cpp`)** is SPI, not I2C — unaffected.

Mitigation: `camera_chan` does init → capture-loop → deinit fully within the
prompt window. `esp_camera_deinit()` + re-acquiring the M5 I2C bus on teardown
restores speaker access. The capture path must **never** assume `sound.cpp` is
available; sound calls during an active camera window are no-ops or deferred.

## Firmware: camera bring-up (verbatim from pinned upstream)

Pinned: `GOB52/M5StackCoreS3_CameraWebServer` @ `58989c64`,
`GOB52/gob_GC0308` @ `a488fc63`. Init order is non-negotiable:

1. `M5.begin()` — already done in `main.cpp setup()`. Powers the camera rail and
   releases the AW9523B-driven camera reset. **No manual P1_0 / AW9523B code** —
   M5Unified owns it. `pin_pwdn = -1`, `pin_reset = -1`.
2. `M5.In_I2C.release()` — immediately before camera init.
3. `esp_camera_init(&camera_config)` — `camera_config_t` pin values copied
   verbatim from the reference file (XCLK=2, SIOD=12, SIOC=11, D0-D7 =
   39,40,41,42,15,16,48,47, VSYNC=46, HREF=38, PCLK=45). `pixel_format =
   PIXFORMAT_RGB565`, `frame_size = FRAMESIZE_QVGA`, `fb_count = 2`,
   `fb_location = CAMERA_FB_IN_PSRAM`, `grab_mode = CAMERA_GRAB_WHEN_EMPTY`,
   `sccb_i2c_port = -1`.
4. `goblib::camera::GC0308::complementDriver()` — exactly once, after init.

Capture loop (verbatim primitives): `esp_camera_fb_get()` →
`frame2jpg(fb, 80, &jpg, &jpg_len)` (RGB565 is never JPEG-native, conversion
always taken) → `esp_camera_fb_return(fb)` → send `jpg` → `free(jpg)` (the
`frame2jpg` buffer is owned by the caller).

`platformio.ini` for `cores3-stackchan*`: add
`-DBOARD_HAS_PSRAM -mfix-esp32-psram-cache-issue`,
`board_build.arduino.memory_type = qio_qspi`, and lib dep
`https://github.com/GOB52/gob_GC0308.git @ ^0.1.0`. Keep the existing CoreS3
board id (do not switch to upstream's `esp32s3box`).

## Firmware: WiFi stream

`wifi_stream.cpp` — `WiFi.begin(ssid, password)` with credentials from
build-time flags (`-DSTACKCHAN_WIFI_SSID=...`, `-DSTACKCHAN_WIFI_PASS=...`) so no
secrets land in the repo; the `cores3-stackchan*` envs read them from a
git-ignored `wifi_secrets.ini` via `extra_configs`, mirroring how the daemon
plist keeps per-machine config out of the repo. Daemon host/port also build-time
flags (default: Mac's LAN IP, a fixed TCP port).

Frame-out: open one TCP socket to the daemon on stream-start, send each frame as
`uint32 length (LE)` + JPEG bytes, close on stream-stop. On socket error, retry a
bounded number of times then give up gracefully (firmware stays usable, prompt
falls back to manual approval on the stick/Desktop).

## Firmware: state machine + return channel

`main.cpp`:
- When a heartbeat sets `state.prompt` (ATTENTION): start `camera_chan` +
  `wifi_stream`. When `state.prompt` clears: tear both down.
- New inbound `{"cmd":"gesture","result":"approve"|"deny"}` — updates ATTENTION
  UI (e.g. flash the character, distinct animation) so the user sees the gesture
  registered. Does **not** itself resolve the prompt — that is the daemon's job.
- New TX: when the daemon confirms a gesture, the **daemon** sends back the
  gesture cmd for UI; the **firmware** emits
  `{"cmd":"permission","id":"<prompt.id>","decision":"approve"|"deny"}` on the
  debug-TX characteristic. Reuse the existing keepalive TX plumbing
  (`g_dbg_tx->setValue` + `notify`). Mirror the PTT mic gesture's
  fire-and-forget shape.

Rationale for firmware-emits-the-ack (not the daemon resolving directly): keeps
the wire contract symmetric with `REFERENCE.md` and means the firmware is the
single source of truth for "the user, at the device, decided X" — same trust
model as the PTT mic gesture.

## Daemon: frame ingest + MediaPipe

`buddy_core/core.py`:
- New asyncio TCP server task, started by `run()`, that accepts the StackChan
  frame stream (length-prefixed JPEG). Only one StackChan connects.
- Decode JPEG → MediaPipe Hands → classify thumbs-up / thumbs-down / none.
  Require the same gesture for a **debounce/hold window** (e.g. N consecutive
  frames or ~0.5s) before it counts — mirrors the PTT gesture's deliberate
  confirm pattern, avoids a flicker approving a tool.
- On confirmed gesture while `state.prompt` is set: send
  `{"cmd":"gesture","result":...}` back over BLE for UI, and route the decision
  into the Claude Code permission resolution path.
- MediaPipe is an optional import — if unavailable, the frame server logs and
  drops frames; gesture-approve degrades to manual approval, nothing crashes.

`cc-bridge/bridge.py` `apply_event`: the gesture decision must reach the same
mechanism that a manual permission approval uses. P1 wires the confirmed
`approve`/`deny` into that path; the exact hook depends on how cc-bridge
currently surfaces the pending prompt to Claude Code (to be confirmed against
`SAFE_TOOLS` gate + the prompt id flow during implementation).

## Testing

- **C++ (`pio test -e native`)** — pure-logic units: the gesture-command parser,
  the state-machine transition (prompt-set → camera-armed flag, prompt-clear →
  disarmed), the TX permission-ack JSON builder. Camera/WiFi hardware calls are
  isolated behind thin seams so the logic is host-testable.
- **Python (`pytest`)** — the gesture debounce/hold classifier (feed synthetic
  landmark sequences), the frame framing/deframing, `apply_event` routing a
  confirmed gesture into the permission path. MediaPipe itself is mocked.
- On-device check — flash, raise a real permission prompt, thumbs-up, confirm the
  tool is approved end-to-end; confirm camera tears down and sound returns after.
