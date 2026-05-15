# 0003 — StackChan camera gesture approve/deny (P0 + P1)

## Why

The StackChan (CoreS3) has a built-in GC0308 camera that the firmware never
touches. The user wants camera-driven features; the agreed first slice (see
`docs/proposals/stackchan-camera.md`) is **P0 — a WiFi camera-stream pipeline**
and **P1 — gesture approve/deny for Claude Code permission prompts** (thumbs-up =
approve, thumbs-down = deny).

Two pieces of infrastructure must be built for this to work at all:

1. **The StackChan firmware is BLE-only.** Streaming camera frames to the Mac
   needs a WiFi path that does not exist.
2. **The firmware never sends events back.** It emits only a
   `{"hello":"stackchan"}` keepalive. There is no firmware→daemon→Claude Code
   return channel — `REFERENCE.md` defines a permission-ack format but nothing
   produces one. Gesture-approve is meaningless without it.

Recognition runs on the Mac (MediaPipe Hands), not on-device — decided in the
proposal doc. The ESP32-S3 stays a thin frame producer.

## What changes

### Firmware (`src/stackchan/`)
- **New `camera_chan.cpp/.h`** — GC0308 bring-up via `esp_camera` (init sequence
  copied verbatim from pinned upstream, see `design.md`), QVGA RGB565 capture,
  `frame2jpg` conversion. Camera is **gated**: only initialised/active while a
  permission prompt is pending.
- **New `wifi_stream.cpp/.h`** — WiFi connect (build-time credentials for P0),
  TCP frame-out loop (4-byte length header + JPEG payload).
- **`main.cpp` state machine** — new `CHAR_*` handling so the camera capture +
  WiFi stream start when `state.prompt` is set (ATTENTION) and stop when it
  clears. New inbound command `{"cmd":"gesture","result":"approve"|"deny"}` from
  the daemon updates UI feedback.
- **`main.cpp` TX path** — emit `{"cmd":"permission","id":"<prompt id>",
  "decision":"approve"|"deny"}` on the existing BLE NUS debug-TX characteristic
  when the daemon reports a confirmed gesture. This is the new return channel.
- **`platformio.ini`** — add `gob_GC0308` lib dep + PSRAM build flags to the
  `cores3-stackchan*` envs.

### Daemon (`tools/`)
- **`buddy_core/core.py`** — new TCP frame-ingest server (accepts the StackChan
  JPEG stream) and a MediaPipe Hands classifier (`thumbs-up`/`thumbs-down` with a
  debounce/hold window). On a confirmed gesture: resolve the pending Claude Code
  permission prompt and send `{"cmd":"gesture","result":...}` back to firmware
  for UI feedback.
- **`cc-bridge/bridge.py` `apply_event`** — wire the gesture result into the
  permission decision path so a thumbs-up actually approves the pending tool.

### Docs
- **`REFERENCE.md`** — document the new `{"cmd":"gesture"}` inbound command, the
  firmware→daemon `{"cmd":"permission"}` ack, the TCP frame stream format, and
  the build-time WiFi credential flags.

## Out of scope

- P2 (attention-aware notifications) and P3 (face tracking) — separate changes.
- On-device ML (evaluated and rejected as primary; see proposal doc).
- WiFi runtime provisioning (dashboard/captive portal) — P0 uses build-time
  credentials; runtime provisioning is deferred.
- cursor-bridge — Claude Code path only for this change.
- Continuous/always-on camera — camera is strictly gated to pending-prompt
  windows for privacy and to bound the I2C-bus-release side effect (see
  `design.md`).
