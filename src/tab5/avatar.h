// clawd GIF avatar for the Tab5 dashboard sidebar.
// Plays the character-pack GIFs (LittleFS /characters/clawd/*) into an
// offscreen canvas; ui.cpp composites that canvas into the frame sprite.
// State numbering matches ui.cpp's AState (ST_IDLE..ST_ERR).
#pragma once
#include <M5Unified.h>

bool avatarInit(uint16_t bgColor);              // mount FS, alloc canvas
void avatarSetState(uint8_t uiState);           // switch GIF on state change
bool avatarTick();                              // decode next frame; true if advanced
void avatarDraw(M5Canvas& dst, int cx, int cy, int outSize = 0); // blit centered; outSize=0 → native
void avatarPushDirect(int cx, int cy, int outSize = 0);          // fast blit straight to LCD
bool avatarReady();                             // false → caller draws fallback
