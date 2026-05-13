#pragma once
// Speaker playback for StackChan buddy firmware.
// Loads a small set of pre-recorded WAV clips (mirrored from
// shanraisshan/claude-code-hooks, ElevenLabs "Samara X" voice) from
// LittleFS into PSRAM at boot, then plays them by name when the daemon
// sends {"play": "<name>"} in a heartbeat. M5Unified's Speaker handles
// async non-blocking playback so calls return immediately.
//
// Clips currently registered (see sound.cpp):
//   "notification"        — generic notification
//   "permissionrequest"   — Claude is asking to approve a tool call

void soundInit();
void soundPlay(const char* name);
