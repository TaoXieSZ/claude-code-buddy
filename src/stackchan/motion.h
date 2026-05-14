#pragma once
#include <stdint.h>

// Non-blocking servo motion controller for StackChan body.
//
// Pairs with character_chan: every CharState gets a corresponding head
// motion pattern (nod, look-around, dance, idle). Driven by a tiny
// state machine in motionTick() that issues M5StackChan.Motion.moveX/Y
// calls at intervals — the BSP handles servo acceleration, so each
// move() returns immediately.
//
// Conservative speeds (200-400, max 1000) keep peak current ≤ ~400mA
// total across both servos so USB-only power doesn't brown out the
// ESP32-S3 BLE radio. Brownouts manifested earlier as random resets
// when audio_ble and servo overlapped on Plus2 — same risk applies here.

void motionInit();

// Switch to motion pattern matching the visual state. Patterns:
//   CHAR_SLEEP     → home + stay
//   CHAR_IDLE      → small periodic look-around
//   CHAR_BUSY      → gentle nod cycle
//   CHAR_ATTENTION → look left-right (worry)
//   CHAR_CELEBRATE → quick dance swing
//   CHAR_DIZZY     → wobble
//   CHAR_HEART     → slow side-to-side
void motionSetState(uint8_t state);

// Drive the active pattern. Call every loop iteration.
void motionTick();

// Runtime config (dashboard-controlled, persisted in NVS by settings.cpp).
// motionSetEnabled(false) parks the servos at home and halts pattern
// playback — used for "quiet mode" desk-share scenarios.
// motionSetIdleWiggle(false) replaces the IDLE pattern with a static
// "stay at home" pattern, leaving all other states animated.
void motionSetEnabled(bool on);
void motionSetIdleWiggle(bool on);
