// motion.cpp — servo dance patterns for StackChan, per CharState.
//
// One pattern per state. Each pattern is a tiny step table consumed
// by motionTick() at intervals — moveX/Y on the BSP are non-blocking
// (they enqueue a servo target + speed and return immediately), so a
// pattern step is "issue move, wait dwell_ms, advance to next step".
//
// Angle units (from BSP docs): X is -1280..1280 (= -128°..+128°, X
// supports continuous 360 too), Y is 0..900 (= 0°..90°). Speed is
// 0..1000. We stay conservative (≤500) for steady patterns and use
// up to 900 only briefly on CELEBRATE.

#include "motion.h"
#include "character_chan.h"   // CharState enum
#include <M5StackChan.h>
#include <Arduino.h>

namespace {

struct Step {
  int16_t x;       // tenths of degrees, -1280..1280
  int16_t y;       // tenths of degrees, 0..900 (kept 0 if N/A)
  uint16_t speed;  // 0..1000
  uint16_t dwell;  // ms to wait after issuing this step
};

// Patterns. Sentinel (dwell=0) marks loop point — when reached, restart.
// Speeds tuned conservatively; one pattern (CELEBRATE) goes fast.
const Step PAT_SLEEP[]     = { {0,   450, 200,  10000}, {0, 0, 0, 0} };
const Step PAT_IDLE[]      = {
  {0,   450, 200, 4000},     // home, breathe
  {300, 500, 250, 1500},     // peek right
  {-300,500, 250, 1500},     // peek left
  {0,   450, 200, 5000},     // home, settle
  {0, 0, 0, 0}
};
const Step PAT_BUSY[]      = {
  // Earlier this was a 600ms-dwell back-and-forth — sounded great
  // visually but the servo never got to silence (motor constantly
  // working) and noise dominated. Smaller amplitude (±10° instead of
  // ±20°), slower speed, and a 3.5s rest step give the same "alive"
  // feel with most of the time spent in actual silence.
  {0, 550, 200, 900},        // gentle up
  {0, 450, 200, 900},        // gentle down
  {0, 500, 200, 3500},       // hold center, rest (quiet)
  {0, 0, 0, 0}
};
const Step PAT_ATTENTION[] = {
  {800, 600, 500, 600},      // look right
  {-800,600, 500, 600},      // look left
  {0, 0, 0, 0}
};
const Step PAT_CELEBRATE[] = {
  {600, 700, 800, 250},
  {-600,700, 800, 250},
  {600, 250, 800, 250},
  {-600,250, 800, 250},
  {0,   500, 400, 800},
  {0, 0, 0, 0}
};
const Step PAT_DIZZY[]     = {
  {500,  300, 700, 250},
  {-500, 700, 700, 250},
  {500,  700, 700, 250},
  {-500, 300, 700, 250},
  {0, 0, 0, 0}
};
const Step PAT_HEART[]     = {
  {400, 550, 200, 1200},
  {-400,550, 200, 1200},
  {0, 0, 0, 0}
};
// "Quiet" pattern used when idle_wiggle is disabled. Single step that
// just sits at home with a long re-arm so the servos never twitch.
const Step PAT_IDLE_QUIET[] = {
  {0, 450, 200, 60000},      // home, sit for a minute, loop
  {0, 0, 0, 0}
};

const Step* PATTERNS[CHAR_N_STATES] = {
  PAT_SLEEP, PAT_IDLE, PAT_BUSY, PAT_ATTENTION,
  PAT_CELEBRATE, PAT_DIZZY, PAT_HEART,
};

// Runtime config — flipped by motionSetEnabled / motionSetIdleWiggle.
// g_master_enabled = false halts all motion (parks at home once).
// g_idle_wiggle = false swaps PAT_IDLE with PAT_IDLE_QUIET in lookup.
bool g_master_enabled = true;
bool g_idle_wiggle    = true;

uint8_t       g_state    = 0xFF;
const Step*   g_pattern  = nullptr;
size_t        g_step_i   = 0;
uint32_t      g_next_at  = 0;
bool          g_running  = false;

void issueStep(const Step& s) {
  ::M5StackChan.Motion.move(s.x, s.y, s.speed);
}

}  // namespace

void motionInit() {
  ::M5StackChan.begin();
  ::M5StackChan.setServoPowerEnabled(true);
  // Go home explicitly so user gets a visible "hello" on boot.
  ::M5StackChan.Motion.goHome();
  g_next_at = millis() + 1000;
}

void motionSetState(uint8_t state) {
  if (state >= CHAR_N_STATES) return;
  if (state == g_state) return;
  g_state   = state;
  // PAT_IDLE_QUIET is swapped in dynamically when idle_wiggle is off.
  if (state == CHAR_IDLE && !g_idle_wiggle) {
    g_pattern = PAT_IDLE_QUIET;
  } else {
    g_pattern = PATTERNS[state];
  }
  g_step_i  = 0;
  g_next_at = 0;   // fire next step on this tick
  g_running = (g_pattern != nullptr);
}

void motionSetEnabled(bool on) {
  g_master_enabled = on;
  if (!on) {
    // Park and stop. Servos stay powered (so they hold home), pattern
    // playback halts.
    ::M5StackChan.Motion.goHome();
    g_running = false;
  } else if (g_state < CHAR_N_STATES) {
    // Resume: recompute pattern for current state.
    motionSetState((uint8_t)(g_state ^ 0xFF));  // force-mismatch
    motionSetState(g_state);
  }
}

void motionSetIdleWiggle(bool on) {
  g_idle_wiggle = on;
  // If currently in IDLE, re-pick the pattern immediately.
  if (g_state == CHAR_IDLE) {
    g_pattern = on ? PAT_IDLE : PAT_IDLE_QUIET;
    g_step_i  = 0;
    g_next_at = 0;
    g_running = true;
  }
}

void motionTick() {
  if (!g_master_enabled) return;
  if (!g_running || !g_pattern) return;
  uint32_t now = millis();
  if (now < g_next_at) return;

  const Step& s = g_pattern[g_step_i];
  // Sentinel (dwell=0) → loop back to start.
  if (s.dwell == 0 && s.speed == 0) {
    g_step_i = 0;
    return;     // re-enter from index 0 next tick
  }
  issueStep(s);
  g_next_at = now + s.dwell;
  g_step_i++;
}
