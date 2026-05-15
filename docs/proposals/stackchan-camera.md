# Proposal: Camera Features for StackChan (CoreS3)

**Status:** Draft for discussion · **Date:** 2026-05-15 · **Author:** deep-interview spec `di-stackchan-camera-20260515`

## TL;DR — Recommendation

Build in this order, gated phase by phase. **Do not commit to all three features up front.**

| Phase | Scope | Effort | Go/No-Go gate |
|-------|-------|--------|---------------|
| **P0** | WiFi camera-stream pipeline (foundation) | ~3–5 days | A GC0308 frame reaches the Mac daemon and MediaPipe returns a result. If WiFi streaming is unreliable, **stop** — every feature depends on this. |
| **P1** | Gesture approve/deny permission prompts | ~4–6 days | Thumbs-up resolves a real Claude Code permission prompt end-to-end. |
| **P2** | Attention-aware notifications | ~2–3 days | Celebrate/nudge fires only when the user faces the device. |
| **P3** | Face tracking (servo follows face) | ~3–4 days | yaw servo tracks the face smoothly within its limited range. |

**Why this order:** P1 is the only feature with real utility (hands-free permission response) and it forces building the firmware→daemon return channel, which is foundational. P2 is cheap and reuses P1's face-landmark output with no servo work. P3 is purely cosmetic, the fiddliest (servo control loop), and lowest value — it ships last or not at all.

**Architecture (locked):** StackChan streams camera frames over **WiFi** to the Mac daemon; the daemon runs **MediaPipe** (hand + face landmarks) and sends results back. On-device ML was evaluated and set aside (see [Alternatives](#alternatives-considered)).

---

## Background

The M5Stack StackChan (CoreS3) target of claude-desktop-buddy currently uses its
LCD, servos, speaker, and a BLE link to the Mac daemon — but **the built-in
camera is completely untouched** (zero references in the codebase). This proposal
evaluates adding camera-driven features.

Three candidate features are in scope (chosen during the deep interview):

1. **Gesture approve/deny** — thumbs-up = approve, thumbs-down = deny a pending
   Claude Code permission prompt.
2. **Attention-aware notifications** — only celebrate/nudge when the user is
   actually looking at the device.
3. **Face tracking** — yaw servo follows the user's face ("looks at you").

Explicitly out of scope: presence detection (the CoreS3 LTR-553 proximity sensor
already covers crude presence), snapshot/selfie features, and any on-device ML as
the primary path.

## Hardware reality

- **Sensor:** GC0308 — 0.3MP (VGA 640×480 max), color, **front-facing** (faces
  the user, above the LCD), DVP parallel interface. Camera reset is on the
  **AW9523B GPIO expander** (P1_0), not a direct pin.
- **No `M5.Camera` API.** M5Unified does not wrap the camera. You drive it via
  Espressif's `esp_camera` directly + a GC0308 driver. Working references:
  [GOB52/M5StackCoreS3_CameraWebServer](https://github.com/GOB52/M5StackCoreS3_CameraWebServer),
  [gob_GC0308 driver](https://github.com/GOB52/gob_GC0308),
  [M5Stack CoreS3 docs (pinmap)](https://docs.m5stack.com/en/core/CoreS3).
- **Resource sharing:** DVP data pins (G15, G16, G39–G48) are camera-dedicated;
  speaker/mic/LCD use separate pins, so coexistence works — but those GPIOs are
  consumed. SCCB control shares the crowded system I2C bus (touch, PMIC, AW9523,
  proximity sensor) — no address conflict, but bus traffic goes up.
- **8MB PSRAM / 16MB flash** — ample for frame buffers and a WiFi stack.

## Two hard prerequisites

Both features and the pipeline depend on infrastructure that **does not exist
today**:

### 1. WiFi path in StackChan firmware
The StackChan firmware is **BLE-only** (`src/stackchan/main.cpp` — BLE NUS,
no WiFi code anywhere). The stream-to-Mac architecture needs a new WiFi
provisioning + frame-streaming path. BLE is ruled out: even 96×96 grayscale JPEG
over the daemon's NUS link is ~1 fps — unusable. WiFi does 10–25 fps QVGA MJPEG
on this board (the CameraWebServer reference). This is the bulk of P0.

### 2. firmware → daemon → Claude Code return channel
Firmware currently sends **only** `{"hello":"stackchan"}` keepalive back to the
daemon (`src/stackchan/main.cpp` TX path). It never emits an event. The wire
protocol in `REFERENCE.md` already defines a permission-ack format
(`{"cmd":"permission","id":...,"decision":...}`), but **firmware never sends it
and the daemon never expects an inbound one**.

The closest existing precedent is the PTT mic gesture: firmware sends
`{"cmd":"mic","state":"down|up"}` and `tools/buddy_core/core.py` turns it into a
macOS keystroke. The return channel for gesture-approve follows the same shape —
this is the bulk of P1's non-CV work.

## Architecture

```
StackChan (CoreS3)                Mac daemon (buddy_core/core.py + cc-bridge)
┌─────────────────────┐           ┌──────────────────────────────────────┐
│ GC0308 → esp_camera │  WiFi     │ frame ingest → MediaPipe              │
│ frame capture loop  │ ───MJPEG─>│   • Hands  → thumbs-up/down           │
│                     │           │   • Face   → position + gaze         │
│ BLE NUS (existing)  │ <──cmd────│ result → apply_event → Claude Code    │
│ servo / LCD / state │           │                                      │
└─────────────────────┘           └──────────────────────────────────────┘
```

- Frame transport: **new** WiFi path on the firmware side.
- Recognition: **MediaPipe on the Mac** — both Hands and Face landmark models are
  mature and run comfortably on a Mac. Near-zero on-device ML work.
- Control/result transport: the **existing BLE NUS link** carries small JSON
  commands back (servo targets for P3, gate flags for P2). The permission-ack
  for P1 needs the new return channel described above.

Keeping recognition on the Mac means all three features share one pipeline and
one CV stack, and the firmware stays a thin frame producer + actuator.

---

## Phased roadmap

### Phase 0 — WiFi camera-stream pipeline (foundation) · ~3–5 days
**Scope:** GC0308 bring-up via `esp_camera`; WiFi provisioning on StackChan;
MJPEG/raw frame stream to the Mac daemon; daemon-side frame ingest + a MediaPipe
smoke test.
**Acceptance criteria:**
- [ ] StackChan captures GC0308 frames without disturbing LCD/servo/speaker.
- [ ] StackChan connects to WiFi (provisioning method decided — likely reuse the
      localhost dashboard or a build-time config).
- [ ] A frame reaches the Mac daemon at ≥10 fps QVGA.
- [ ] The daemon runs one MediaPipe model on a received frame and logs a result.
**Gate:** if WiFi streaming is unreliable or fps is too low, **stop and rethink**
— every downstream feature depends on this.

### Phase 1 — Gesture approve/deny · ~4–6 days
**Scope:** MediaPipe Hands on the daemon → classify thumbs-up / thumbs-down;
build the firmware→daemon→Claude Code permission-ack return channel; gate camera
capture to only run while a permission prompt is pending (StackChan ATTENTION
state); on-device UX feedback (e.g. ATTENTION animation reacts to detection).
**Acceptance criteria:**
- [ ] When Claude Code raises a permission prompt, thumbs-up approves it and
      thumbs-down denies it, end-to-end.
- [ ] Camera only runs while a prompt is pending (privacy + power).
- [ ] False-positive rate is low enough to trust (define a debounce / hold
      window, mirror the PTT gesture's confirm pattern).
- [ ] Latency from gesture to resolution is acceptable (target < 1.5s).
**Why first:** only feature with real utility; forces the return channel that is
otherwise un-built.

### Phase 2 — Attention-aware notifications · ~2–3 days
**Scope:** MediaPipe Face on the daemon → "is the user facing the device?"
boolean; daemon gates celebrate/nudge events on that flag; StackChan defers
non-urgent state changes until the user looks back.
**Acceptance criteria:**
- [ ] Celebrate/nudge fires only when the user faces the device.
- [ ] Deferred notifications resolve correctly when the user looks back.
- [ ] No regression to existing state-machine behaviour when the camera is off.
**Why second:** cheap, reuses P1's MediaPipe Face output, no servo work.

### Phase 3 — Face tracking · ~3–4 days
**Scope:** MediaPipe Face position → yaw servo target; smoothing/deadband
control loop on the firmware; clamp to the yaw servo's limited range (it cannot
rotate 360°).
**Acceptance criteria:**
- [ ] yaw servo tracks the face smoothly within its mechanical range.
- [ ] No jitter or oscillation; graceful behaviour when the face leaves frame.
- [ ] Tracking yields to higher-priority motion patterns (BUSY, CELEBRATE).
**Why last:** purely cosmetic, fiddliest (servo control loop), lowest value.
Reasonable to defer indefinitely.

---

## Alternatives considered

| Alternative | Verdict | Rationale |
|-------------|---------|-----------|
| **On-device ML** (ESP-DL / Edge Impulse classifier on ESP32-S3) | Rejected as primary | Feasible (~0.7–0.9s latency, proven on the same SoC) but: face tracking would be choppy, LCD GIF rendering competes for CPU, and gesture models need self-collected training data. Stream-to-Mac gives realtime + mature MediaPipe for near-zero ML effort. Keep as a fallback only if offline/no-WiFi operation becomes a requirement. |
| **Crude on-device CV** (frame differencing, skin-color blobs) | Rejected | Cannot distinguish hand *shapes* — only wave/presence. Not viable for thumbs-up vs thumbs-down. |
| **Stream frames over BLE** | Rejected | ~1 fps even at 96×96 grayscale. WiFi is mandatory for streaming. |
| **Presence detection** (wake/sleep on sit-down/leave) | Out of scope | Not selected by the user; the CoreS3 LTR-553 proximity sensor already covers crude presence without a camera. |

## Open questions for discussion

1. **WiFi provisioning** — build-time credentials, a captive portal, or extend
   the existing localhost dashboard? Affects P0 effort.
2. **Privacy posture** — P1 gates the camera to "prompt pending only." Is that
   the rule for all phases, or do P2/P3 need it always-on? An always-on camera
   on a desktop pet is a real UX/trust question worth deciding explicitly.
3. **Is P3 worth doing at all?** It is pure decoration with the highest fiddliness.
   Consider shipping P0–P2 and stopping.
4. **Power/thermal** — continuous WiFi streaming + camera on the CoreS3: does the
   device run hot or drain fast? Worth a quick measurement during P0.

## References

- [M5Stack CoreS3 docs — pinmap & camera](https://docs.m5stack.com/en/core/CoreS3)
- [GOB52/M5StackCoreS3_CameraWebServer](https://github.com/GOB52/M5StackCoreS3_CameraWebServer)
- [gob_GC0308 GC0308 driver](https://github.com/GOB52/gob_GC0308)
- [Espressif — Hand Gesture Recognition on ESP32-S3 with ESP-DL](https://developer.espressif.com/blog/hand-gesture-recognition-on-esp32-s3-with-esp-deep-learning/)
- [CNX Software writeup](https://www.cnx-software.com/2022/12/07/hand-gesture-recognition-on-esp32-s3-with-the-esp-dl-library/)
- Codebase: `src/stackchan/main.cpp` (BLE RX/TX, state machine), `tools/buddy_core/core.py` (daemon, PTT mic precedent), `REFERENCE.md` (wire protocol, permission-ack format)
- Interview spec: `.omc/specs/deep-interview-stackchan-camera.md`
