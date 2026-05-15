# Architecture

This is the "how the whole thing fits together" doc — diagrams of the
data paths, daemon process model, firmware state machine, and the new
camera-gesture pipeline. Use it as a map when you're trying to find the
right module to change.

For surface-level details (how to flash, pair, wire) see the [README](../README.md).
For the wire-format contract see [REFERENCE.md](../REFERENCE.md).

---

## 1. System overview

Every supported IDE producer (Claude Desktop, Claude Code CLI, Cursor)
ultimately drives the same firmware over the same Nordic UART BLE
service. The differences live in how Mac-side events get turned into
the heartbeat payload the stick expects.

```mermaid
flowchart LR
    classDef ide       fill:#fef3c7,stroke:#b45309,color:#000
    classDef daemon    fill:#dbeafe,stroke:#1d4ed8,color:#000
    classDef firmware  fill:#dcfce7,stroke:#15803d,color:#000
    classDef hw        fill:#fce7f3,stroke:#be185d,color:#000

    CD["Claude Desktop<br/>(official GUI)"]:::ide
    CC["Claude Code<br/>(terminal CLI)"]:::ide
    CR["Cursor IDE"]:::ide

    BR1["upstream BLE bridge<br/>(in-app)"]:::daemon
    BR2["cc-bridge<br/>tools/cc-bridge/"]:::daemon
    BR3["cursor-bridge<br/>tools/cursor-bridge/"]:::daemon

    STICK["Plus2 stick<br/>(1.14&quot; LCD + buzzer + BugC2)"]:::firmware
    SC["StackChan / CoreS3<br/>(2.0&quot; LCD + servos + LEDs + speaker)"]:::firmware

    HW["BLE NUS<br/>(encrypted + debug)"]:::hw

    CD -->|in-app| BR1
    CC -->|hook events<br/>via Unix socket| BR2
    CR -->|Node shim<br/>via Unix socket| BR3

    BR1 -.heartbeat JSON.-> HW
    BR2 -.heartbeat JSON.-> HW
    BR3 -.heartbeat JSON.-> HW

    HW -->|"Claude-XXXX<br/>Claude-SC-XXXX"| STICK
    HW -->|"Cursor-XXXX<br/>Cursor-SC-XXXX"| SC

    STICK -.cmd:permission<br/>cmd:mic.-> HW
    SC -.cmd:permission<br/>cmd:gesture<br/>cmd:mic.-> HW
```

The daemons pick which stick to talk to via the BLE advertising name
prefix (`Claude-`, `Cursor-`). Two daemons running side by side never
fight because they scan for different prefixes.

---

## 2. One heartbeat tick

When a hook event arrives at the daemon, the daemon mutates `BuddyState`
(`tools/buddy_core/core.py`) and emits a fresh JSON line to the stick.
The stick parses the line, maps it to a character state, and updates
the display.

```mermaid
sequenceDiagram
    autonumber
    participant IDE as Claude Code / Cursor
    participant Hook as hook.py / hook.js
    participant Daemon as cc-bridge / cursor-bridge
    participant State as BuddyState
    participant BLE as BleWriter
    participant FW as Stick firmware
    participant LCD as Display

    IDE->>Hook: pre/post tool, prompt submit, stop, ...
    Hook->>Daemon: JSON line on Unix socket
    Daemon->>State: apply_event(state, ev)
    Note over State: running++ / waiting=1 / msg, etc.
    Daemon->>BLE: state.to_payload()
    BLE->>FW: BLE NUS notify (JSON)
    FW->>FW: applyJsonLine — parse, mapState
    FW->>LCD: character + status bar
    Note over FW: 10s of silence → SLEEP face
    FW-->>BLE: keepalive every 10s
```

The "lifecycle" events (`UserPromptSubmit`, `Stop`, `PreToolUse`,
`PostToolUse`, `PermissionRequest`, `SessionEnd`) drive the persona
state machine. `hud` events are pure telemetry — they carry
context-window %, token counts, rate-limit % from Claude Code's
statusline stdin and never touch session lifecycle counters.

---

## 3. The daemon process model

`buddy_core.run()` is the shared shell every daemon shares. Each daemon
plugs IDE-specific behaviour (`apply_event`, optional extra tasks) into
it via injection. cc-bridge currently has the richest set of tasks
because it also runs the localhost dashboard and the camera frame
ingest server.

```mermaid
flowchart TB
    classDef task     fill:#e0e7ff,stroke:#4338ca,color:#000
    classDef pipe     fill:#fef3c7,stroke:#b45309,color:#000
    classDef shared   fill:#dcfce7,stroke:#15803d,color:#000

    subgraph daemon["buddy_core.run() event loop — cc-bridge"]
        direction TB
        S0["unix socket server<br/>(hooks come in)"]:::task
        S1["heartbeat_loop<br/>(emit on dirty)"]:::task
        S2["reconnect_loop<br/>(BLE backoff)"]:::task
        S3["frame_server<br/>(camera JPEG ingest)"]:::task
        S4["reaper_loop<br/>(drop stale sessions)"]:::task
        S5["dashboard HTTP<br/>(localhost:18765)"]:::task
    end

    State["BuddyState"]:::shared
    Pending["pending<br/>(rid→Future)"]:::shared
    BLE["BleWriter (bleak)"]:::shared

    S0 -->|apply_event| State
    S0 -->|set| Pending
    S3 -->|reads state.prompt| State
    S3 -->|"ble.write<br/>cmd:gesture"| BLE
    S4 -->|recompute counters| State
    S1 -->|to_payload| State
    S1 -->|notify| BLE
    BLE -->|on_stick_line| Pending
    Pending -.resolves.-> S0
```

Key invariants worth remembering when changing daemon code:

- **Dirty flag**: any task can set `dirty` to force the next heartbeat
  emit immediately. Otherwise heartbeat runs on its 10-second keepalive
  schedule.
- **Single BLE writer**: only `BleWriter.write` touches the stick —
  every task funnels through it.
- **Permission acks come back**: the stick sends
  `{"cmd":"permission","id","decision"}` via BLE NUS TX. `on_stick_line`
  resolves the matching `pending[rid]` future, which unblocks the
  `_handle_wait_permission` coroutine and answers Claude Code.

---

## 4. Firmware state machine

The stick is a state machine driven by the daemon's heartbeat. Seven
states, one renderer per target hardware.

```mermaid
stateDiagram-v2
    [*] --> SLEEP

    SLEEP --> IDLE: any heartbeat
    IDLE --> BUSY: msg contains "thinking" / "running" / state.running > 0
    BUSY --> ATTENTION: state.prompt set or msg contains "approve"
    ATTENTION --> BUSY: prompt clears, running still > 0
    ATTENTION --> IDLE: prompt clears, running == 0
    BUSY --> CELEBRATE: msg contains "done" (tool finished)
    CELEBRATE --> IDLE: 3s timeout
    IDLE --> SLEEP: 20s of no heartbeats
    BUSY --> SLEEP: 20s of no heartbeats
    ATTENTION --> SLEEP: 20s of no heartbeats

    note right of CELEBRATE
      Holds 3s so the dance
      animation plays out
      before the next BUSY
      cuts it short.
    end note

    note right of ATTENTION
      Priority state: even
      mid-CELEBRATE a fresh
      prompt interrupts.
    end note
```

State → output mapping per target:

| State | Plus2 sprite | BugC2 motion | StackChan face | StackChan body |
|-------|--------------|--------------|----------------|----------------|
| SLEEP | sleeping zZz | motors off | sleep gif | servos home, idle wiggle off |
| IDLE | idle blink | gentle LED breathing | idle gif | head tilt, optional wiggle |
| BUSY | working | slow forward nudge | busy gif | small nods |
| ATTENTION | alert | LEDs strobe yellow | attention gif | look-left-right |
| CELEBRATE | jump | spin in place + LEDs cycle | celebrate gif | 4× yaw swing + look-up |
| DIZZY | wobble | wiggle | dizzy gif | tilt off-axis |
| HEART | heart eyes | warm LED pulse | heart gif | gentle bob |

---

## 5. Camera gesture pipeline (StackChan, new)

The camera is **gated** — only runs while a permission prompt is
pending. This bounds the I2C-bus side effect (see §6) and is the privacy
posture for the feature.

```mermaid
sequenceDiagram
    autonumber
    participant CC as Claude Code
    participant Daemon as cc-bridge daemon
    participant State as BuddyState
    participant BLE as BLE NUS
    participant FW as StackChan firmware
    participant Cam as GC0308 + WiFi stream
    participant MP as MediaPipe Hands (Mac)

    CC->>Daemon: PermissionRequest (Bash, ...)
    Daemon->>State: state.prompt = {id, tool}
    Daemon->>BLE: heartbeat with prompt
    BLE->>FW: applyJsonLine — latch g_prompt_id
    FW->>FW: ATTENTION + shouldCameraBeArmed → Arm
    FW->>Cam: cameraStart + wifiStreamStart
    loop every ~100ms while armed
        FW->>Cam: cameraCaptureJpeg + frame2jpg
        Cam->>Daemon: TCP: [u32 len][JPEG]
    end
    Daemon->>MP: classify_jpeg(payload)
    MP-->>Daemon: "approve" / "deny" / None
    Note over Daemon: GestureClassifier debounce (N=5)
    Daemon->>BLE: cmd:gesture (result:approve)
    BLE->>FW: ATTENTION UI flash
    FW->>BLE: cmd:permission (id, decision:once)
    BLE->>Daemon: on_stick_line → resolve future
    Daemon->>CC: decision=once → unblock tool
    CC->>Daemon: PreToolUse → prompt clears
    Daemon->>BLE: heartbeat with no prompt
    BLE->>FW: applyJsonLine — g_prompt_id cleared
    FW->>FW: shouldCameraBeArmed → Disarm
    FW->>Cam: wifiStreamStop + cameraStop
    Note over FW: I2C bus re-acquired, sound returns
```

The "wire decision" string is `"once"` for approve and `"deny"` for
deny — this is what `_handle_wait_permission` replies to Claude Code,
matching the existing Plus2 A-button approve flow. Firmware emits the
ack (not the daemon) so the firmware stays the single source of truth
for "the user, at the device, decided X".

If `wifi_secrets.ini` still has placeholder credentials, the
`shouldCameraBeArmed` check short-circuits and the camera never even
starts — no I2C release, no speaker mute, no failed WiFi associate
per prompt. Manual approval (Desktop GUI, etc.) keeps working.

---

## 6. CoreS3 hardware quirks: shared I2C bus

The GC0308 camera's SCCB control lines live on GPIO11/12 — which is
**also the internal system I2C bus** on the CoreS3. Pinned upstream
firmware ([GOB52/M5StackCoreS3_CameraWebServer](https://github.com/GOB52/M5StackCoreS3_CameraWebServer))
solves this by calling `M5.In_I2C.release()` before camera init so
esp32-camera privately owns those two GPIOs. That works, but while it's
in effect M5Unified can't reach any of these chips:

```mermaid
flowchart TB
    classDef cam    fill:#fef3c7,stroke:#b45309,color:#000
    classDef shared fill:#fce7f3,stroke:#be185d,color:#000
    classDef ok     fill:#dcfce7,stroke:#15803d,color:#000

    BUS["CoreS3 internal I2C bus<br/>GPIO 11 (SCL) / 12 (SDA)"]:::shared

    CAM["GC0308 camera<br/>SCCB control"]:::cam
    AMP["AW88298<br/>speaker amplifier"]:::shared
    RTC["BM8563 RTC"]:::shared
    IMU["BMI270 IMU"]:::shared
    TCH["FT6336 touch"]:::shared
    AW["AW9523B GPIO expander<br/>(incl. CAM reset)"]:::shared
    PMIC["AXP2101 PMIC"]:::shared

    BUS --- CAM
    BUS --- AMP
    BUS --- RTC
    BUS --- IMU
    BUS --- TCH
    BUS --- AW
    BUS --- PMIC

    SERVO["Servos (LEDC PWM)"]:::ok
    LCD["LCD (SPI)"]:::ok
    LED["RGB LEDs (RMT)"]:::ok

    note1["While camera active:<br/>❌ sound (sound.cpp deferred)<br/>❌ touch / RTC / IMU<br/>✅ servos (separate peripheral)<br/>✅ LCD (separate bus)<br/>✅ RGB LEDs (separate peripheral)"]

    BUS -.- note1
```

Mitigation in the firmware:
- **`cameraStop()`** calls `M5.In_I2C.begin()` to reacquire the bus on
  prompt-clear. Speaker, touch, RTC all come back.
- **`sound.cpp`** treats playback as unavailable during the camera
  window — calls become no-ops or get deferred.
- **Camera window is short** (the duration of one permission prompt,
  typically a few seconds), so the side effect is bounded.

The reset pin for the GC0308 is on the AW9523B GPIO expander
(P1_0 = CAM_RST), but **upstream's firmware doesn't touch it** — the
plain `M5.begin()` brings up the AW9523 and releases the camera reset
as part of CoreS3 board init. We follow the same pattern (`pin_pwdn =
-1`, `pin_reset = -1`).

---

## 7. File map

The repo is split into firmware (C++), daemons (Python), and prep
tooling. The diagram below shows the modules that get touched most.

```mermaid
flowchart LR
    classDef fw    fill:#dcfce7,stroke:#15803d,color:#000
    classDef daemon fill:#dbeafe,stroke:#1d4ed8,color:#000
    classDef shared fill:#fef3c7,stroke:#b45309,color:#000
    classDef test  fill:#fce7f3,stroke:#be185d,color:#000

    subgraph plus2["src/ — Plus2 firmware"]
        MAIN["main.cpp"]:::fw
        BUGC["bugc2.cpp"]:::fw
        BLE_P["ble_bridge.cpp"]:::fw
        CHAR["character.cpp"]:::fw
        DATA["data.h"]:::fw
        COMPAT["m5_compat.h"]:::fw
    end

    subgraph sc["src/stackchan/ — StackChan firmware"]
        SMAIN["main.cpp"]:::fw
        SCHAR["character_chan.cpp"]:::fw
        SSND["sound.cpp"]:::fw
        SMOT["motion.cpp"]:::fw
        SSET["settings.cpp"]:::fw
        SCAM["camera_chan.cpp"]:::fw
        SWIF["wifi_stream.cpp"]:::fw
        SARM["camera_arm.h"]:::fw
        SACK["permission_ack.h"]:::fw
        SFR["frame_framing.h"]:::fw
    end

    subgraph daemons["tools/ — daemons"]
        CORE["buddy_core/core.py<br/>(BuddyState, BleWriter, run)"]:::shared
        CCB["cc-bridge/bridge.py"]:::daemon
        CRB["cursor-bridge/bridge.py"]:::daemon
        FSV["buddy_core/frame_server.py"]:::shared
        FDF["buddy_core/frame_deframer.py"]:::shared
        HG["buddy_core/hand_gesture.py"]:::shared
        GC["buddy_core/gesture_classifier.py"]:::shared
        DASH["cc-bridge/dashboard.py"]:::daemon
    end

    subgraph tests["test/ + tests/ — host-only logic units"]
        CPP_T["pio test -e native:<br/>color_util, frame_framing,<br/>camera_arm, permission_ack"]:::test
        PY_T["pytest:<br/>buddy_core, cc_bridge,<br/>cursor_bridge, statusline_hud,<br/>frame_deframer, frame_server,<br/>gesture_classifier, hand_gesture"]:::test
    end

    CCB -.uses.-> CORE
    CRB -.uses.-> CORE
    CCB -.uses.-> FSV
    CCB -.uses.-> HG
    HG -.uses.-> GC
    FSV -.uses.-> FDF
    CCB -.serves.-> DASH

    SMAIN -.includes.-> SCAM
    SMAIN -.includes.-> SWIF
    SMAIN -.includes.-> SARM
    SMAIN -.includes.-> SACK
    SWIF -.includes.-> SFR
```

The dashed arrows are "imports / includes". The four host-testable
headers in `src/stackchan/` (`camera_arm.h`, `permission_ack.h`,
`frame_framing.h`, plus `color_util.h` shared with `src/`) keep the
firmware seam thin enough that the native test env covers the logic
without an ESP32 board.

---

## See also

- [REFERENCE.md](../REFERENCE.md) — wire-format contract (heartbeat
  schema, BLE service UUIDs, command vocabulary).
- [docs/states.md](states.md) — persona state machine semantics.
- [docs/controls.md](controls.md) — Plus2 button + gesture map.
- [docs/proposals/stackchan-camera.md](proposals/stackchan-camera.md) —
  the original opinionated proposal that became the camera-gesture
  feature.
- [openspec/specs/](../openspec/specs/) — formal specs in OpenSpec
  format (`daemon-event-mapping`, `camera-gesture-pipeline`).
