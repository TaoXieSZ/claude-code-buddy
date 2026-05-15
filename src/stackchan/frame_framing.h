// Pure-logic framing helpers for the StackChan camera-stream wire format
// (P0 of openspec/changes/2026-05-15-0003-stackchan-camera-gestures).
//
// Frames go out as: <4-byte little-endian length><JPEG bytes>. The header
// builder lives in this header so the native test env can compile it
// without pulling Arduino / esp_camera / WiFi. The actual TCP send +
// camera grab live in wifi_stream.cpp / camera_chan.cpp.

#pragma once

#include <stdint.h>

// Write the 32-bit JPEG payload length as 4 little-endian bytes into out[0..3].
// LE matches the daemon's `struct.unpack("<I", ...)` deframer (Python side).
inline void writeFrameLengthLE(uint32_t len, uint8_t out[4]) {
    out[0] = static_cast<uint8_t>(len & 0xFFu);
    out[1] = static_cast<uint8_t>((len >> 8) & 0xFFu);
    out[2] = static_cast<uint8_t>((len >> 16) & 0xFFu);
    out[3] = static_cast<uint8_t>((len >> 24) & 0xFFu);
}
