#pragma once
#include <stdint.h>

// CoreS3-tailored character renderer for the StackChan buddy firmware.
// Plays the same /characters/<pack>/*.gif files Plus2 uses, but draws
// directly to M5.Lcd in portrait (240×320) at 2× scale with a 26px
// status bar reserved at the bottom for daemon msg text.
//
// Why a new file instead of porting src/character.cpp:
//   - character.cpp is welded to `extern M5Canvas spr;` (Plus2's sprite)
//     and hardcodes Plus2 sprite math (140px upper region, 70px peek).
//   - Avoids dragging in the peek/info-page/text-mode code paths that
//     don't apply on CoreS3.
// The AnimatedGIF callback shape is reused verbatim — that part is
// upstream-library API, not project-specific.

enum CharState : uint8_t {
  CHAR_SLEEP = 0,
  CHAR_IDLE,
  CHAR_BUSY,       // randomly cycles busy_0 / busy_1 / busy_2
  CHAR_ATTENTION,
  CHAR_CELEBRATE,
  CHAR_DIZZY,
  CHAR_HEART,
  CHAR_N_STATES
};

// Mount LittleFS, load /characters/<name>/manifest.json (bg color),
// remember base path. Call AFTER M5.begin(). Returns false on FS error
// or missing pack. Pass nullptr to autodetect the first pack on disk.
bool characterInit(const char* name);

// Hot-swap the character pack at runtime. Re-runs characterInit
// internals with a new pack name (or nullptr for autodetect), then
// re-opens the GIF for the current state so the change is visible
// immediately. Used by the dashboard's "character pack" dropdown.
void characterReload(const char* name);

// Switch active GIF. No-op when state unchanged. CHAR_BUSY picks a
// random busy_N each call so repeated busy → animation variety.
void characterSetState(uint8_t state);

// Advance frame timing, decode next frame if due. Call every loop tick.
void characterTick();

// Bottom status bar — msg line. Repainted lazily on change.
// nullptr or "" clears the line to bg color.
void characterSetMsg(const char* msg);

// Bottom status bar — stats line ("R:N W:N  tok:Xk"). Repainted lazily.
// Pass tool=nullptr/empty when no tool is active.
void characterSetStats(int running, int waiting, uint32_t tokens, const char* tool);
