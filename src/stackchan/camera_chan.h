// GC0308 camera bring-up + capture for the StackChan (CoreS3).
// P0 of openspec/changes/2026-05-15-0003-stackchan-camera-gestures.
//
// The camera is GATED — only initialised while a Claude Code permission prompt
// is pending (state.prompt set). main.cpp's state machine calls cameraStart()
// when ATTENTION begins and cameraStop() when it ends.
//
// Side effect (load-bearing): cameraStart() calls M5.In_I2C.release() so
// esp32-camera can privately own GPIO11/12 for SCCB. While the camera is
// active, M5Unified cannot reach the AW88298 speaker amp / IMU / RTC / touch.
// sound.cpp MUST treat playback as unavailable in this window — see
// openspec/changes/.../specs/camera-gesture-pipeline/spec.md.
//
// Init sequence is VERBATIM from pinned upstream
// (GOB52/M5StackCoreS3_CameraWebServer @ 58989c64 + GOB52/gob_GC0308 @
// a488fc63) — see cores3-camera-upstream-reference.md in the same change dir.

#pragma once

#include <stddef.h>
#include <stdint.h>

// Initialise the GC0308. Caller must have already called M5.begin() (main.cpp
// setup does). Returns true on ESP_OK, false on any failure. On failure the
// camera is left deinitialised and the M5 I2C bus is re-acquired so the rest
// of the firmware stays usable (prompt falls back to manual approval).
bool cameraStart();

// Tear down: esp_camera_deinit() + re-acquire the M5 I2C bus so sound.cpp /
// touch / RTC / IMU work again. Safe to call when already stopped.
void cameraStop();

// True if cameraStart() succeeded and cameraStop() has not been called since.
bool cameraIsActive();

// Grab one frame, convert RGB565 → JPEG (GC0308 has no JPEG hardware so
// conversion is always taken). On success *out_buf points at a malloc'd JPEG
// buffer of *out_len bytes — caller MUST free(*out_buf) after sending. On
// failure returns false and *out_buf / *out_len are left untouched.
bool cameraCaptureJpeg(uint8_t** out_buf, size_t* out_len);
