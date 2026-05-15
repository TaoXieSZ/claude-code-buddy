# Roadmap

Living list of what's shipped lately and what's queued. The [OpenSpec
archive](../openspec/changes/archive/) is the authoritative timeline
for behaviour changes; this file is the human-readable summary.

## Recently shipped

- **StackChan camera gesture pipeline** (PRs #14, #15→#17, #19) —
  GC0308 camera streams JPEG over WiFi during a permission prompt;
  MediaPipe Hands on the Mac recognises thumbs-up/down; firmware
  emits the matching permission ack over BLE. Gated to prompt
  windows for privacy + to bound the I²C-bus-release side effect.
  See [`docs/architecture.md`](architecture.md#5-camera-gesture-pipeline-stackchan-new).
- **cc-bridge session reaper** (PR #16) — `state.running` stuck
  above zero after a dropped `Stop` event used to mean a daemon
  restart. Now a 10-min staleness reaper rebuilds counters
  automatically; the daemon self-heals.
- **HUD metrics from the statusline** (archived OpenSpec change
  0002) — context %, real token counts, rate-limit %, model name
  surface on the stick via Claude Code's statusline stdin.
- **CELEBRATE swing dance** — yaw servo can't rotate 360° on the
  CoreS3 StackChan; the celebrate motion is now a tasteful 4-swing
  dance instead of an unreachable spin.

## Open

### Cursor parity
- **Cursor permission echo** — Cursor's tool-approval UI lives
  inside the IDE; there's no hook event for an external daemon to
  answer yes/no. Need to investigate an IDE extension or CLI flag.

### BugC2 chassis (Plus2)
- **LED-as-status overlay** — RGB LEDs currently mirror persona
  state mood. They could double as a token-rate indicator
  (brightness proportional to recent tokens/sec).
- **Auto-calibration for motor asymmetry** — manual tool exists
  (`tools/motor-calib.html`); a one-shot self-test that drives a
  known pattern and uses IMU yaw drift to compute trim would beat
  the current "click 8 buttons and eyeball straightness".
- **Self-calibrating turn** — the original turn-around-and-walk-back
  motion was hard to pace because battery sag changes the 180°
  duration. Currently replaced with an in-place spin. An IMU-closed-loop
  turn would revive the original idea.
- **Cliff detection** — chassis can walk off a desk during
  translation. An ultrasonic / IR add-on, or a hard travel-distance
  cap with IMU integration, would harden it.

### Character packs
- **More GIF packs** from `clawd-on-desk` — calico, cloudling.
  `tools/prep_character.py` already supports per-state bounding
  boxes; just need a manifest.

### StackChan camera gestures
- **On-device E2E hardware verification** — gesture path is
  wire-complete but only verified to "ATTENTION fires" against the
  real device. Full thumbs-up-approves-Bash flow needs a real WiFi
  + a re-flash + a session in front of the StackChan camera.
- **Classifier robustness** — `classify_landmarks` is a simple
  fingers-folded + thumb-y-vs-index-MCP heuristic. Rotation
  invariance and lighting tolerance would benefit from a tested
  corpus of real device frames.
- **More gesture vocabulary** — open palm, OK sign, fist could
  trigger other commands (skip, ask, cancel).

## Out of scope (researched, not feasible without a hardware change)

- **Jumping / hopping** on BugC2. Chassis spec confirms only 4 DC
  motors + 2 RGB LEDs. The "springs" on the chassis are passive
  wheel suspension.
- **On-device gesture recognition** on the CoreS3 ESP32-S3. Tasks
  API requires a model file and ~50 ms of inference per frame —
  feasible but the wifi-stream-to-Mac path is dramatically simpler
  and was chosen for that reason. Documented decision in
  [`docs/proposals/stackchan-camera.md`](proposals/stackchan-camera.md).

---

Want to contribute? Pick an "Open" item, open an OpenSpec change
proposal (`/opsx:propose` or by hand following the shape of
[any archived change](../openspec/changes/archive/)), and put up a
PR. Small, focused PRs land fastest.
