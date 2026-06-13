// Tab5 dashboard UI (P1 layout C: sidebar + main + clawd avatar).
// Self-contained DEMO model for the design pass — uiTick() animates fake
// sessions through every visual state so the look can be judged on-device.
// M1 swaps the demo model for live TCP feeds; the render code stays.
#pragma once
#include <stdint.h>

// Live device status fed from main.cpp each tick.
struct UiStatus {
  bool wifiUp;
  char ip[20];
  int  rssi;
  int  battPct;
  int  micLevel;   // 0..100, drives the avatar mouth
};

void uiInit();
void uiTick(const UiStatus& st);

// ── live-feed API (M1 / M3 dual-feed) ────────────────────────────────
// feed.cpp routes each heartbeat to a session by its "app" tag:
//   sess 0 = Claude Code, sess 1 = Cursor. The first uiFeedAlive() for any
// session latches live mode and retires the demo script.
void uiFeedAlive(int sess);
void uiFeedState(int sess, uint8_t state, const char* tool, uint32_t tokens);
void uiFeedLine(int sess, const char* line);
void uiFeedPrompt(int sess, bool pending, const char* id, const char* text);
// Pops one queued touch/keyboard verdict (Allow/Deny). Returns false when
// empty. appOut receives the source app ("claude"/"cursor") so the daemon
// hub can route the ack back to the originating bridge.
bool uiTakeDecision(char* idOut, unsigned idCap, bool* allow,
                    char* appOut, unsigned appCap);

// main.cpp hook for the serial feed pump (implemented in feed.cpp).
void feedPoll();

// Dev tool: stream the current full-frame sprite back over serial as a framed
// base64 RGB565 screenshot (SHOT <w> <h> <rawLen> … ENDSHOT). Triggered by
// {"cmd":"shot"}. Implemented in ui.cpp (it owns the sprite).
void uiScreenshot();

// Tab5-as-Mac-input control channel (implemented in feed.cpp). PTT dictation
// reuses the stick's {"cmd":"mic","state":"down|up"} which the daemon relays
// to the Mac dictation hotkey via _send_key.
void feedSendMic(bool down);
// True while the hold-to-talk button is held (main.cpp streams mic audio then).
bool uiMicHeld();
// Stream a captured PCM chunk to the daemon as an `A<base64>` audio frame.
void feedSendAudio(const int16_t* pcm, int n);
// Tab5 keyboard → Mac second keyboard. Translates a HID usage code + modifier
// byte into a {"cmd":"key",...} line; the daemon types it into the Mac.
void feedSendKey(uint8_t hidKey, uint8_t mods);

// USB keyboard (kbd.cpp): HID usage code + modifier byte, called from loop().
void uiKeyEvent(uint8_t hidKey, uint8_t mods);
void kbdInit();
void kbdPoll();
