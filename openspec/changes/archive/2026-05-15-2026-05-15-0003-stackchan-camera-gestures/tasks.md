# Tasks

## P0 — WiFi camera-stream pipeline

- [ ] `platformio.ini`: add `gob_GC0308` lib dep + PSRAM flags
      (`-DBOARD_HAS_PSRAM -mfix-esp32-psram-cache-issue`,
      `board_build.arduino.memory_type = qio_qspi`) to `cores3-stackchan*` envs;
      wire build-time WiFi/host flags via git-ignored `wifi_secrets.ini`.
- [ ] `src/stackchan/camera_chan.cpp/.h`: GC0308 init (verbatim sequence from
      `cores3-camera-upstream-reference.md`), QVGA RGB565 capture, `frame2jpg`,
      `cameraStart()` / `cameraStop()` with full `esp_camera_deinit()` +
      M5 I2C re-acquire on stop.
- [ ] `src/stackchan/wifi_stream.cpp/.h`: `WiFi.begin(ssid,pass)`, TCP connect to
      daemon, length-prefixed JPEG frame-out, bounded retry + graceful give-up.
- [ ] `buddy_core/core.py`: asyncio TCP frame-ingest server task, length-prefixed
      JPEG deframing, started from `run()`.
- [ ] Tests (P0): C++ native — frame framing builder; Python — frame
      deframing roundtrip. `make test` green.
- [ ] On-device gate check: prompt-pending → camera inits, a frame reaches the
      daemon at ≥10 fps QVGA; prompt clears → camera deinits, sound returns.
      **If WiFi streaming is unreliable, stop and reassess before P1.**

## P1 — Gesture approve/deny

- [ ] `buddy_core/core.py`: MediaPipe Hands classifier (optional import),
      thumbs-up/down detection with a debounce/hold window.
- [ ] `src/stackchan/main.cpp`: state machine arms `camera_chan` + `wifi_stream`
      on `state.prompt` set, tears down on clear.
- [ ] `src/stackchan/main.cpp`: inbound `{"cmd":"gesture","result":...}` handler
      → ATTENTION UI feedback.
- [ ] `src/stackchan/main.cpp`: TX `{"cmd":"permission","id","decision"}` on the
      debug-TX characteristic when the daemon confirms a gesture.
- [ ] `buddy_core/core.py`: on confirmed gesture, send `{"cmd":"gesture"}` back
      for UI; `cc-bridge/bridge.py` `apply_event`: route the confirmed
      approve/deny into the Claude Code permission resolution path.
- [ ] Tests (P1): C++ native — gesture-cmd parser, prompt→armed transition,
      permission-ack JSON builder; Python — debounce classifier on synthetic
      landmarks, `apply_event` gesture routing (MediaPipe mocked). `make test`
      green.
- [ ] On-device E2E: raise a real Claude Code permission prompt, thumbs-up
      approves the tool, thumbs-down denies; camera tears down after.

## Wrap-up

- [ ] `REFERENCE.md`: document `{"cmd":"gesture"}` inbound, `{"cmd":"permission"}`
      firmware→daemon ack, TCP frame-stream format, build-time WiFi flags.
- [ ] `pio run -e cores3-stackchan-claude` + `pio test -e native` + `pytest` all
      green; firmware build matrix unaffected.
- [ ] `openspec archive 0003-stackchan-camera-gestures`.
