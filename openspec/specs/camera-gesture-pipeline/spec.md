# camera-gesture-pipeline Specification

## Purpose
TBD - created by archiving change 2026-05-15-0003-stackchan-camera-gestures. Update Purpose after archive.
## Requirements
### Requirement: Camera lifecycle is bound to the permission-prompt window

The StackChan firmware MUST initialise the GC0308 camera and the WiFi frame
stream only while a Claude Code permission prompt is pending, and MUST tear both
down when the prompt clears. The camera MUST NOT run at any other time.

#### Scenario: Prompt arrives
- GIVEN a StackChan with no pending permission prompt and the camera inactive
- WHEN a heartbeat sets `state.prompt` (firmware enters ATTENTION)
- THEN `cameraStart()` and the WiFi frame stream are started

#### Scenario: Prompt clears
- GIVEN a StackChan with the camera active for a pending prompt
- WHEN a heartbeat clears `state.prompt`
- THEN `cameraStop()` runs `esp_camera_deinit()`, the WiFi stream socket is
  closed, and the M5Unified I2C bus is re-acquired

#### Scenario: No prompt, no camera
- GIVEN a StackChan in any non-ATTENTION state
- WHEN the firmware loop runs
- THEN the camera stays deinitialised and no frames are streamed

### Requirement: Camera bring-up follows the pinned upstream sequence

The firmware MUST initialise the GC0308 using the verbatim sequence from
`cores3-camera-upstream-reference.md`: `M5.begin()` first, then
`M5.In_I2C.release()`, then `esp_camera_init()` with the upstream
`camera_config_t` pin values (RGB565, QVGA, `fb_count=2`, PSRAM, `sccb_i2c_port
= -1`), then `goblib::camera::GC0308::complementDriver()` exactly once.

#### Scenario: Camera init succeeds
- GIVEN a CoreS3 StackChan after `M5.begin()`
- WHEN `cameraStart()` runs
- THEN `M5.In_I2C.release()` is called, `esp_camera_init()` returns `ESP_OK`,
  and `complementDriver()` is called once

#### Scenario: Camera init fails
- GIVEN a CoreS3 StackChan where `esp_camera_init()` returns non-`ESP_OK`
- WHEN `cameraStart()` runs
- THEN the failure is logged, the camera is left deinitialised, the M5 I2C bus is
  re-acquired, and the firmware stays usable (prompt falls back to manual approval)

### Requirement: Sound is unavailable while the camera is active

The firmware MUST treat sound playback as unavailable while the camera is active.
Because the camera privately owns the GPIO11/12 I2C bus (`M5.In_I2C.release()`),
`sound.cpp` calls MUST be safe no-ops or deferred until after `cameraStop()`, and
sound MUST NOT be assumed available during a camera window.

#### Scenario: Sound requested during a camera window
- GIVEN a StackChan with the camera active for a pending prompt
- WHEN `soundPlay()` is called
- THEN it does not crash or hang; the request is a no-op or deferred until after
  `cameraStop()`

#### Scenario: Servos keep running during a camera window
- GIVEN a StackChan with the camera active in ATTENTION
- WHEN the motion tick runs
- THEN the LEDC-PWM servo pattern continues unaffected (servos are not on the
  released I2C bus)

### Requirement: WiFi frame stream format

While the camera is active, the firmware MUST connect a single TCP socket to the
daemon and send each captured frame as a 4-byte little-endian length header
followed by the JPEG payload produced by `frame2jpg`.

#### Scenario: Frame sent
- GIVEN an active camera window with a connected daemon socket
- WHEN a frame is captured and converted via `frame2jpg(fb, 80, ...)`
- THEN a `uint32` little-endian length is written, then the JPEG bytes, then the
  `frame2jpg` buffer is `free()`d and the camera frame buffer is returned

#### Scenario: Socket error
- GIVEN an active camera window
- WHEN the daemon socket write fails
- THEN the firmware retries a bounded number of times then gives up gracefully,
  leaving the rest of the firmware functional

### Requirement: Gesture result UI feedback

The firmware MUST accept an inbound `{"cmd":"gesture","result":"approve"|"deny"}`
command and give the user visible ATTENTION-state feedback that the gesture
registered. This command MUST NOT itself resolve the permission prompt.

#### Scenario: Gesture command received
- GIVEN a StackChan in ATTENTION with the camera active
- WHEN `{"cmd":"gesture","result":"approve"}` arrives
- THEN the ATTENTION UI reflects the registered gesture (distinct animation /
  flash) and no permission decision is made by this handler alone

### Requirement: Firmware emits the permission ack

When the daemon confirms a gesture, the firmware MUST emit
`{"cmd":"permission","id":"<pending prompt id>","decision":"approve"|"deny"}` on
the debug-TX BLE NUS characteristic, reusing the existing keepalive TX plumbing.
The firmware is the single source of truth for an at-device user decision.

#### Scenario: Confirmed gesture produces an ack
- GIVEN a pending prompt with a known `id` and a daemon-confirmed gesture
- WHEN the firmware processes the confirmation
- THEN it emits `{"cmd":"permission","id":"<id>","decision":...}` via
  `g_dbg_tx->notify()`

#### Scenario: No pending prompt
- GIVEN a StackChan with no pending prompt
- WHEN a gesture confirmation somehow arrives
- THEN no permission ack is emitted

