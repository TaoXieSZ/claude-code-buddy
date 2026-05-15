// Pure-logic helpers driving the StackChan camera/wifi-stream lifecycle.
// P1 of openspec/changes/2026-05-15-0003-stackchan-camera-gestures.
//
// Kept header-only + inline so the native test env can compile them without
// any Arduino / M5Unified / esp_camera deps. main.cpp's loop() composes
// these to decide when to call cameraStart()/cameraStop() and
// wifiStreamStart()/wifiStreamStop().

#pragma once

// True iff the camera should be running right now. All three conditions
// must hold:
//   - ATTENTION state (a permission prompt is pending)
//   - We know the prompt's id (without it we can't emit the permission ack)
//   - WiFi credentials are real (not the wifi_secrets.ini placeholder)
//
// The last guard skips the cameraStart/wifiStreamStart/cameraStop bounce
// when creds aren't configured — otherwise every permission prompt would
// briefly mute the speaker (I2C release → reacquire) for no gain, since
// the stream would fail to connect anyway.
inline bool shouldCameraBeArmed(bool attention_state,
                                bool has_prompt_id,
                                bool wifi_creds_present) {
    return attention_state && has_prompt_id && wifi_creds_present;
}

// Edge detection vs the prior tick. main.cpp keeps a static `prev_armed`
// and acts on Arm (call cameraStart + wifiStreamStart) / Disarm (call
// wifiStreamStop + cameraStop).
enum class ArmTransition { None, Arm, Disarm };

inline ArmTransition cameraTransition(bool prev_armed, bool now_armed) {
    if (now_armed && !prev_armed) return ArmTransition::Arm;
    if (!now_armed && prev_armed) return ArmTransition::Disarm;
    return ArmTransition::None;
}
