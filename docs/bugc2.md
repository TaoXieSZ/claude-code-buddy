# BugC2 chassis (optional)

If you mount the Plus2 stick on a BugC2 base, the firmware drives the
chassis to mirror the buddy's persona state. The base brings 4 DC
motors, 2 RGB LEDs, and an STM32F030F4P6 over I2C 0x38.

## State → motion mapping

| Persona state | BugC2 motion + LED                                              |
|---------------|------------------------------------------------------------------|
| `sleep`       | motors off, LEDs off                                             |
| `idle`        | motors off, LEDs dim cyan                                        |
| `busy`        | 1.2s in-place spin + 3-chirp ascending bleep (900/1300/1700 Hz)  |
| `attention`   | 80ms twitch every ~1.2s, amber LED breathing pulse               |
| `celebrate`   | continuous gentle spin, green LEDs                               |
| `dizzy`       | quick alternating spin, yellow LEDs (capped at 600ms)            |
| `heart`       | pink heartbeat (thump-thump) on LEDs + occasional small wiggle   |

I2C protocol verified verbatim against
[`m5stack/M5Hat-BugC@c054b6e`](https://github.com/m5stack/M5Hat-BugC).
The driver uses Arduino `Wire` (I2C_NUM_0) at 400 kHz on G0/G26 —
**not** `Wire1` which would collide with M5Unified's IMU/RTC bus.

## Manual motor calibration

`tools/motor-calib.html` is a Web Bluetooth page that connects to the
stick over the existing NUS service and sends raw 4-channel motor
commands (`{"cmd":"motor","s":[a,b,c,d]}`). Useful for figuring out
which channel drives which wheel, finding the FORWARD pattern, and
tuning per-side speed trim if your motors are asymmetric.

```bash
cd tools
python3 -m http.server 8765
open http://localhost:8765/motor-calib.html
```

Connect, then sliders / WASD / preset buttons send commands. Auto-stop
after 1500 ms of no keepalive. Manual mode suspends the persona-state
mapping so the operator owns the chassis.

## Hardware notes

- Stick boots fine without BugC2 — the driver probes 0x38 at startup
  and skips silently if not present.
- BugC2 covers BtnB physically. Firmware auto-detects this at boot and
  flips to **no-B mode** for button semantics — see
  [controls.md](controls.md).
- Servo power on the BugC2 is fed from the Plus2's battery rail; on
  USB-only with brand-new servos brownout is unlikely but not zero.
  Reduce simultaneous motor amplitude if you see resets.
