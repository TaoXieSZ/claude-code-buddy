// WiFi + TCP frame-out for the StackChan camera-stream (P0 of
// openspec/changes/2026-05-15-0003-stackchan-camera-gestures).
//
// Wire format: <uint32 LE length><JPEG bytes>, matching
// tools/buddy_core/frame_deframer.py on the daemon side. Length builder
// is the pure-logic helper in frame_framing.h.
//
// WiFi credentials + daemon host/port come from build-time -D flags driven
// by wifi_secrets.ini (see platformio.ini [platformio] extra_configs). If
// the placeholders are left in place, wifiStreamStart() logs and returns
// false — manual permission approval keeps working.
//
// Lifecycle (called from main.cpp's state machine on prompt windows):
//   prompt set    → cameraStart(), wifiStreamStart()
//   each frame    → cameraCaptureJpeg(...), wifiStreamSendFrame(...)
//   prompt clear  → cameraStop(),  wifiStreamStop()
// WiFi stays up across prompt windows; only the TCP socket cycles.

#pragma once

#include <stddef.h>
#include <stdint.h>

// Bring up WiFi if needed, open the TCP socket to the daemon. Returns true
// on a connected socket. Tolerates being called when already connected
// (no-op). Bounded retries internally; on terminal failure logs and
// returns false without blocking the rest of the firmware.
bool wifiStreamStart();

// Send one length-prefixed JPEG frame. Returns false on socket error or if
// not connected; the caller's loop should treat that as a transient miss
// (the prompt remains resolvable via manual approval).
bool wifiStreamSendFrame(const uint8_t* jpg, size_t len);

// Close the TCP socket. Leaves WiFi associated (cheaper than reassociating
// on the next prompt). Safe to call when already stopped.
void wifiStreamStop();

// True between a successful wifiStreamStart() and the next wifiStreamStop()
// or a detected socket error.
bool wifiStreamIsConnected();

// True iff wifi_secrets.ini has been edited from its tracked placeholder.
// main.cpp's loop() checks this before arming the camera — skips the
// cameraStart/Stop bounce (and the brief speaker mute) when there's no
// way the stream could succeed anyway.
bool wifiStreamCredsAvailable();
