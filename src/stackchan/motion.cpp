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
// Y geometry: BSP doc has Y range 0..900 = 0°..90° where 0 looks DOWN
// (chin to chest) and 900 looks straight UP. The original patterns hovered
// around y=450 (mid) — visually the face was tilted half-way down, which
// means the LCD is angled away from a desk-sitting user. Bumped all
// baselines into the 700–850 range so the screen always faces up at the
// human. Sub-state variation (nod/look-around) still happens, just stays
// in the "head up" half of the workspace.
const Step PAT_SLEEP[]     = { {0,   780, 200,  10000}, {0, 0, 0, 0} };
const Step PAT_IDLE[]      = {
  {0,   800, 200, 4000},     // home (head up), breathe
  {300, 820, 250, 1500},     // peek right
  {-300,820, 250, 1500},     // peek left
  {0,   800, 200, 5000},     // back to head-up center, settle
  {0, 0, 0, 0}
};
const Step PAT_BUSY[]      = {
  // ±10° amplitude around the head-up baseline; same slow speed +
  // 3.5s rest as before so the servos still get quiet between nods.
  {0, 850, 200, 900},        // gentle nod-up
  {0, 750, 200, 900},        // gentle nod-down (still above mid)
  {0, 800, 200, 3500},       // hold head-up center, rest
  {0, 0, 0, 0}
};
const Step PAT_ATTENTION[] = {
  {800, 850, 500, 600},      // look right + alert head-up
  {-800,850, 500, 600},      // look left + alert head-up
  {0, 0, 0, 0}
};
const Step PAT_CELEBRATE[] = {
  {600, 870, 800, 250},
  {-600,870, 800, 250},
  {600, 750, 800, 250},
  {-600,750, 800, 250},
  {0,   820, 400, 800},
  {0, 0, 0, 0}
};
const Step PAT_DIZZY[]     = {
  {500,  720, 700, 250},
  {-500, 870, 700, 250},
  {500,  870, 700, 250},
  {-500, 720, 700, 250},
  {0, 0, 0, 0}
};
const Step PAT_HEART[]     = {
  {400, 820, 200, 1200},
  {-400,820, 200, 1200},
  {0, 0, 0, 0}
};
// "Quiet" pattern when idle_wiggle is disabled. Head-up rest position,
// long re-arm so the servos never twitch.
const Step PAT_IDLE_QUIET[] = {
  {0, 800, 200, 60000},      // head-up center, hold for a minute, loop
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
  // BSP's goHome() parks at (0, 0) — Y=0 looks straight down, hiding
  // the screen from a desk-sitting user. We override to (0, 800) so
  // the head defaults to looking up, screen presented to the user.
  ::M5StackChan.Motion.move(0, 800, 250);
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
    // Park at head-up so disabling motion still leaves the screen
    // facing the user, not the desk. Servos stay powered to hold pose.
    ::M5StackChan.Motion.move(0, 800, 250);
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
