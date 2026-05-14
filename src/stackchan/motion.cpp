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
  int16_t y;       // signed delta from g_y_baseline, tenths of degrees
  uint16_t speed;  // 0..1000 (positioned move); ignored when rotate != 0
  uint16_t dwell;  // ms to wait after issuing this step
  int16_t rotate;  // 0 → positioned move(x,y,speed). non-zero → continuous
                   // rotateX(rotate): yaw spins at that velocity (-1000..
                   // 1000, +CCW/-CW) until the next positioned step stops
                   // it. Used for the CELEBRATE 360° spin. x/y/speed are
                   // ignored on a rotate step.
};

// Patterns. Sentinel (dwell=0) marks loop point — when reached, restart.
// Speeds tuned conservatively; one pattern (CELEBRATE) goes fast.
// Y geometry note: BSP range is 0..900 = 0°..90°, 0=looks down, 900=straight up.
// Step.y is now a SIGNED DELTA from the user-configurable head-up baseline
// (g_y_baseline, default 650 = 65°). issueStep() adds the delta and clamps
// to [0, 900]. Earlier we used absolute Y values centred on 800 — that put
// the head all the way against its mechanical stop ("顶天了" feedback), and
// there was no way to dial it back without a reflash. Dashboard slider now
// owns the baseline; patterns just contribute sub-state motion around it.
const Step PAT_SLEEP[]     = { {0,   -20, 200,  10000}, {0, 0, 0, 0} };
const Step PAT_IDLE[]      = {
  {0,     0, 200, 4000},     // baseline, breathe
  {300,  20, 250, 1500},     // peek right, tiny tilt-up
  {-300, 20, 250, 1500},     // peek left, tiny tilt-up
  {0,     0, 200, 5000},     // back to baseline, settle
  {0, 0, 0, 0}
};
const Step PAT_BUSY[]      = {
  // ±5° around baseline + 3.5 s rest — same quiet pacing as before.
  {0,  50, 200, 900},        // gentle nod-up
  {0, -50, 200, 900},        // gentle nod-down
  {0,   0, 200, 3500},       // hold baseline, rest (quiet)
  {0, 0, 0, 0}
};
const Step PAT_ATTENTION[] = {
  {800,  50, 500, 600},      // look right, alert lift
  {-800, 50, 500, 600},      // look left, alert lift
  {0, 0, 0, 0}
};
// CELEBRATE — a happy wiggle, then 转一圈: a full 360° spin to express
// joy. rotateX() is continuous (a velocity command, not a positioned
// move), so the spin runs for SPIN_MS then the next positioned step
// halts it. SPIN_MS is the dwell that yields ~one revolution at
// SPIN_VELOCITY — the exact rev count depends on the feedback servo's
// free-run speed, so tune SPIN_MS on-device if it over/undershoots.
// main.cpp holds CELEBRATE long enough (see g_celebrate_until) for the
// spin to finish before falling back to IDLE.
constexpr int16_t  SPIN_VELOCITY = 650;    // CCW; negate for CW
constexpr uint16_t SPIN_MS       = 1500;   // ≈ one full circle at v=650
const Step PAT_CELEBRATE[] = {
  {0,    40, 600, 200,  0},              // chin up — wind-up
  {0,     0,   0, SPIN_MS, SPIN_VELOCITY}, // 转一圈! continuous 360° spin
  {0,    20, 500, 400,  0},              // positioned move halts the spin
  {500,  60, 800, 220,  0},              // happy wiggle right
  {-500, 60, 800, 220,  0},              // wiggle left
  {0, 0, 0, 0, 0}
};
const Step PAT_DIZZY[]     = {
  {500, -80, 700, 250},
  {-500, 70, 700, 250},
  {500,  70, 700, 250},
  {-500,-80, 700, 250},
  {0, 0, 0, 0}
};
const Step PAT_HEART[]     = {
  {400,  20, 200, 1200},
  {-400, 20, 200, 1200},
  {0, 0, 0, 0}
};
// "Quiet" pattern when idle_wiggle is disabled — sits at baseline.
const Step PAT_IDLE_QUIET[] = {
  {0, 0, 200, 60000},        // baseline, hold for a minute, loop
  {0, 0, 0, 0}
};

// Runtime-tunable head-up baseline (tenths of degrees, 0..900).
// Default 650 = 65° tilt-up; user changes via dashboard.
int16_t g_y_baseline = 650;

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
  // A rotate step puts the yaw servo into continuous-rotation mode at
  // the given velocity; it keeps spinning until a later positioned step
  // (move) issues a position command on the same servo and halts it.
  if (s.rotate != 0) {
    ::M5StackChan.Motion.rotateX(s.rotate);
    return;
  }
  // s.y is a signed delta from the configurable baseline (in tenths of
  // degrees). Clamp the absolute target to the BSP's [0, 900] range so
  // a high baseline + a "tilt up" pattern step doesn't slam past the
  // mechanical stop.
  int y = (int)g_y_baseline + (int)s.y;
  if (y < 0)   y = 0;
  if (y > 900) y = 900;
  ::M5StackChan.Motion.move(s.x, y, s.speed);
}

}  // namespace

void motionInit() {
  ::M5StackChan.begin();
  ::M5StackChan.setServoPowerEnabled(true);
  // Park at baseline (head-up) so the very first move presents the
  // screen. BSP's goHome would park at Y=0 (chin-to-chest); we don't
  // want that even momentarily.
  ::M5StackChan.Motion.move(0, g_y_baseline, 250);
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
    // Park at baseline (head-up) so "quiet mode" still presents the
    // screen. Servos stay powered to hold pose.
    ::M5StackChan.Motion.move(0, g_y_baseline, 250);
    g_running = false;
  } else if (g_state < CHAR_N_STATES) {
    // Resume: recompute pattern for current state.
    motionSetState((uint8_t)(g_state ^ 0xFF));  // force-mismatch
    motionSetState(g_state);
  }
}

void motionSetTilt(uint8_t deg) {
  if (deg > 90) deg = 90;
  g_y_baseline = (int16_t)deg * 10;   // 0..90° → 0..900 in BSP units
  // Re-issue current pattern step at the new baseline immediately so
  // the head visibly responds to the slider without waiting for the
  // next pattern step.
  if (g_running && g_pattern && g_step_i > 0) {
    issueStep(g_pattern[g_step_i - 1]);
  } else {
    ::M5StackChan.Motion.move(0, g_y_baseline, 250);
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
  // Sentinel ({0,0,0,0,0}) → loop back to start. A rotate step also has
  // speed=0, so the sentinel must check rotate too — otherwise a
  // dwell-0 rotate step would be misread as the loop marker.
  if (s.dwell == 0 && s.speed == 0 && s.rotate == 0) {
    g_step_i = 0;
    return;     // re-enter from index 0 next tick
  }
  issueStep(s);
  g_next_at = now + s.dwell;
  g_step_i++;
}
