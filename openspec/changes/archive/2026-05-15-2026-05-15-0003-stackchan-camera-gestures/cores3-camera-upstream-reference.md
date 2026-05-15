# CoreS3 GC0308 Camera — Verbatim Upstream Reference

Pinned commits:
- `GOB52/M5StackCoreS3_CameraWebServer` @ `58989c64d46654690f8b9d664381ebd4637b1731` (branch `master`)
- `GOB52/gob_GC0308` @ `a488fc636d60b792e9af939a5f4cfbca9a962adf` (branch `master`, library version `0.1.0`)

Key surprise vs. your assumption: **GOB52's working port does NOT touch the AW9523B at all.**
It sets `pin_pwdn = -1` and `pin_reset = -1` and relies entirely on `M5.begin()` to bring up the
AW9523B and release the camera reset line. There is no manual P1_0 assert in the upstream ground-truth
code. The "reset via AW9523B P1_0" fact is correct hardware-wise (M5Stack docs confirm
`AW9523B P1_0 = CAM_RST`), but M5Unified's `M5.begin()` handles it — esp32-camera never sees the reset pin.

---

## 1. GC0308 Camera Bring-Up

### 1a. Pin defines + camera_config_t (the active `#else` branch — `USING_EXISTING_I2C` is NOT defined)

Source: `src/main.cpp`
URL: https://github.com/GOB52/M5StackCoreS3_CameraWebServer/blob/58989c64d46654690f8b9d664381ebd4637b1731/src/main.cpp

```cpp
#include <M5Unified.h>
#include <esp_camera.h>
#include <WiFi.h>
#include <gob_GC0308.hpp>

// --------------------------------
// Camera GC0308
// Pin settings
#define CAM_PIN_PWDN    -1
#define CAM_PIN_RESET   -1
#define CAM_PIN_XCLK    2
#define CAM_PIN_SIOD    12
#define CAM_PIN_SIOC    11
#define CAM_PIN_D7      47
#define CAM_PIN_D6      48
#define CAM_PIN_D5      16
#define CAM_PIN_D4      15
#define CAM_PIN_D3      42
#define CAM_PIN_D2      41
#define CAM_PIN_D1      40
#define CAM_PIN_D0      39
#define CAM_PIN_VSYNC   46
#define CAM_PIN_HREF    38
#define CAM_PIN_PCLK    45

extern void startCameraServer(); // app_httpd.cpp

//#define USING_EXISTING_I2C

#if defined(USING_EXISTING_I2C)
// ... (NOT compiled — see note 1c below)
#else
static camera_config_t camera_config =
{
    .pin_pwdn     = -1,
    .pin_reset    = -1,
    .pin_xclk     = 2,
    .pin_sscb_sda = 12,
    .pin_sscb_scl = 11,
    .pin_d7 = 47,
    .pin_d6 = 48,
    .pin_d5 = 16,
    .pin_d4 = 15,
    .pin_d3 = 42,
    .pin_d2 = 41,
    .pin_d1 = 40,
    .pin_d0 = 39,
    .pin_vsync = 46,
    .pin_href  = 38,
    .pin_pclk  = 45,
    .xclk_freq_hz = 20000000,
    .ledc_timer   = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    .pixel_format = PIXFORMAT_RGB565,
    //.frame_size   = FRAMESIZE_QQVGA,
    .frame_size   = FRAMESIZE_QVGA,
    .jpeg_quality = 0,
    .fb_count     = 2,
    .fb_location  = CAMERA_FB_IN_PSRAM,
    .grab_mode    = CAMERA_GRAB_WHEN_EMPTY,
    .sccb_i2c_port = -1,
};
#endif
```

Notes:
- `pin_xclk = 2` — XCLK driven on GPIO2 by the ESP32 LEDC peripheral. (M5Stack docs list XCLK as `-1`
  meaning "no dedicated header pin," but the firmware still has to generate it on GPIO2.)
- `pixel_format = PIXFORMAT_RGB565` (NOT JPEG — GC0308 has no JPEG hardware; `jpeg_quality = 0` is
  effectively unused). `frame_size = FRAMESIZE_QVGA` (320x240).
- `fb_count = 2`, `fb_location = CAMERA_FB_IN_PSRAM`, `grab_mode = CAMERA_GRAB_WHEN_EMPTY`.
- `sccb_i2c_port = -1` → esp32-camera creates its OWN I2C bus on `pin_sscb_sda=12 / pin_sscb_scl=11`.
  This only works because `M5.In_I2C.release()` is called first (see 1b).
- Field order in this struct literal matters — it must match the `camera_config_t` definition in
  whichever esp32-camera version M5Unified bundles.

### 1b. setup() — exact init sequence (the compiled `#else` path)

Source: `src/main.cpp` (same URL)

```cpp
void setup()
{
    // M5
    M5.begin();
    M5.Log.setEnableColor(m5::log_target_serial, false);

    M5.Display.clear(TFT_ORANGE);
#if defined(USING_EXISTING_I2C)
    camera_config.sccb_i2c_port = M5.In_I2C.getPort();
    esp_err_t err = esp_camera_init(&ccfg);
#else
    M5.In_I2C.release();
    esp_err_t err = esp_camera_init(&camera_config);
#endif
    if (err != ESP_OK)
    {
        M5.Display.clear(TFT_BLUE);
        M5_LOGE("Failed to init camera:%d", err);
        delay(1000 * 10);
        abort();
    }
    if(!goblib::camera::GC0308::complementDriver())
    {
        M5_LOGE("F...");   // (truncated in fetch) — complementDriver failure path
        // ...
    }
    // ... WiFi.begin() ...
    // Server
    startCameraServer();
    M5_LOGI("Camera ready use: http;//%s to connect", WiFi.localIP().toString().c_str());

    M5.Display.clear(TFT_DARKGREEN);
}

void loop()
{
    //    M5_LOGI("%u", esp_get_free_heap_size());
    delay(10000);
}
```

**Order of operations (ground truth):**
1. `M5.begin()` FIRST — plain, no custom config. This powers the camera rail and releases the
   AW9523B-driven camera reset. There is NO manual P1_0 / AW9523B code anywhere.
2. `M5.In_I2C.release()` — releases M5Unified's internal I2C bus so esp32-camera can take over
   GPIO11/12 for its own SCCB bus (the `sccb_i2c_port = -1` path).
3. `esp_camera_init(&camera_config)`.
4. `goblib::camera::GC0308::complementDriver()` — MUST be called exactly once, after
   `esp_camera_init()`. Patches the esp32-camera GC0308 sensor driver.
5. `WiFi.begin()` then `startCameraServer()`.

So: **camera init comes AFTER `M5.begin()`.** `M5.begin()` takes no special config — the only
camera-related M5Unified call is `M5.In_I2C.release()` between begin and init.

### 1c. complementDriver — what it does + minimal usage

Source: `src/gob_GC0308.hpp`
URL: https://github.com/GOB52/gob_GC0308/blob/a488fc636d60b792e9af939a5f4cfbca9a962adf/src/gob_GC0308.hpp

```cpp
namespace goblib { namespace camera {
namespace GC0308
{
/*!
  @brief complement esp32-camera GC0308 driver
  @warning Must be call after esp_camera_init() once.
  @note Delete set_gain_ctrl
  @note Add set_agc_gain
  @note Add set_specia_effect
  @note Add set_wb_mode
  @note Add set_saturation
  @note Replace set_contrast
  @retval true Success
  @retval false Failure
*/
bool complementDriver();
}
}}
```

Minimal usage pattern from gob_GC0308 README:

```cpp
#include <esp_camera.h>
#include <gob_GC0308.hpp>

void setup()
{
    camera_config_t ccfg{};
    // Configuration settings...
    esp_camera_init(&ccfg);
    goblib::camera::GC0308::complementDriver(); // Must be call after esp_camera_init()
}
```

This library complements/patches the esp32-camera GC0308 driver (and adds an optional QR recognizer).
`complementDriver()` is NOT a substitute for `esp_camera_init()` — esp32-camera still provides the
GC0308 driver; gob_GC0308 only fixes/extends it.

### 1d. Which library provides the GC0308 driver

- The **GC0308 sensor driver itself ships inside esp32-camera**, which is bundled by **M5Unified**
  (`lib_deps = m5stack/M5Unified`). There is no standalone `esp32-camera` entry in `lib_deps`.
- `gob_GC0308 @ ^0.1.0` is a *complement/patch* layer, pulled directly from git.

`platformio.ini` `lib_deps` (verbatim):
```ini
lib_deps = m5stack/M5Unified
  https://github.com/GOB52/gob_GC0308.git @ ^0.1.0
lib_ldf_mode = deep
```

`gob_GC0308` `library.json` (verbatim) — note its own transitive dep:
```json
{
  "name": "gob_GC0308",
  "version": "0.1.0",
  "headers": "gob_GC0308.hpp",
  "platforms": "espressif32",
  "frameworks": "arduino",
  "dependencies": {
    "ESP32QRCodeReader": "https://github.com/alvarowolfx/ESP32QRCodeReader.git"
  }
}
```

---

## 2. WiFi + Camera Frame Streaming

### 2a. WiFi connect (verbatim, from CameraWebServer README "WiFi 接続ができない時")

```cpp
// main.cpp
    WiFi.begin(); // Connects to credential stored in the hardware.
```
Or, to hardcode:
```cpp
// main.cpp
    WiFi.begin("Your SSID", "Your password");
```
GOB52's port relies on credentials already stored in NVS by default. For your firmware you'll want the
explicit `WiFi.begin(ssid, password)` form.

### 2b. Frame grab → JPEG → return loop

Source: `src/app_httpd.cpp` (the MJPEG stream handler)
URL: https://github.com/GOB52/M5StackCoreS3_CameraWebServer/blob/58989c64d46654690f8b9d664381ebd4637b1731/src/app_httpd.cpp

The core grab/convert/return sequence, verbatim:

```cpp
        fb = esp_camera_fb_get();
        if (!fb)
        {
            log_e("Camera capture failed");
            res = ESP_FAIL;
        }
        else
        {
            _timestamp.tv_sec = fb->timestamp.tv_sec;
            _timestamp.tv_usec = fb->timestamp.tv_usec;
            fr_start = esp_timer_get_time();
            // ...
            if (fb->format != PIXFORMAT_JPEG)
            {
                bool jpeg_converted = frame2jpg(fb, 80, &_jpg_buf, &_jpg_buf_len);
                esp_camera_fb_return(fb);
                fb = NULL;
                if (!jpeg_converted)
                {
                    log_e("JPEG compression failed");
                    res = ESP_FAIL;
                }
            }
            else
            {
                _jpg_buf_len = fb->len;
                _jpg_buf = fb->buf;
            }
        }
```

**Critical for GC0308:** since `pixel_format = PIXFORMAT_RGB565`, `fb->format != PIXFORMAT_JPEG` is
ALWAYS true → you MUST call `frame2jpg(fb, quality, &buf, &len)` to get JPEG bytes. `frame2jpg` quality
here is `80`. After `frame2jpg` succeeds you own `_jpg_buf` and must `free(_jpg_buf)` once sent;
`esp_camera_fb_return(fb)` is called right after conversion. If the format were already JPEG you'd
return the fb only after sending.

### 2c. Minimal raw frame-out loop (adapted from the verbatim pieces above)

The upstream only ships the full HTTP MJPEG server. The simplest reliable frame-out path, built
strictly from the verbatim primitives above:

```cpp
// after WiFi connected; client = a connected WiFiClient (TCP) to your host
camera_fb_t *fb = esp_camera_fb_get();
if (fb) {
    uint8_t *jpg = nullptr; size_t jpg_len = 0;
    if (fb->format != PIXFORMAT_JPEG) {
        bool ok = frame2jpg(fb, 80, &jpg, &jpg_len);  // GC0308 path: always taken
        esp_camera_fb_return(fb);
        if (ok) {
            // send a 4-byte length header then the JPEG payload
            uint32_t n = jpg_len;
            client.write((uint8_t*)&n, 4);
            client.write(jpg, jpg_len);
            free(jpg);                                  // frame2jpg buffer is yours
        }
    } else {
        client.write(fb->buf, fb->len);
        esp_camera_fb_return(fb);                       // return AFTER send for JPEG-native sensors
    }
}
```

Only `esp_camera_fb_get` / `esp_camera_fb_return` / `frame2jpg` are upstream-verbatim; the framing/socket
glue is your call. The discipline that IS load-bearing: with RGB565 you always convert, and `frame2jpg`'s
buffer must be `free()`d by you.

### 2d. PSRAM / fb settings for CoreS3 (8MB PSRAM)

From the verbatim `camera_config`: `fb_location = CAMERA_FB_IN_PSRAM`, `fb_count = 2`,
`grab_mode = CAMERA_GRAB_WHEN_EMPTY`. CoreS3's 8MB PSRAM easily holds 2 QVGA RGB565 buffers
(320*240*2 = 150KB each). `fb_count = 2` comment in the `USING_EXISTING_I2C` branch:
`// CPU Loads too much but faster`. Drop to `fb_count = 1` if you want lower CPU at the cost of
throughput.

---

## 3. platformio.ini + Gotchas

### 3a. platformio.ini (verbatim, GOB52 port)

URL: https://github.com/GOB52/M5StackCoreS3_CameraWebServer/blob/58989c64d46654690f8b9d664381ebd4637b1731/platformio.ini

```ini
[env]
platform = espressif32@6.2.0
framework = arduino

board_build.flash_mode = qio
board_build.f_flash = 80000000L

lib_deps = m5stack/M5Unified
  https://github.com/GOB52/gob_GC0308.git @ ^0.1.0
lib_ldf_mode = deep

monitor_speed = 115200
monitor_filters = esp32_exception_decoder, time
upload_speed = 921600

build_flags = -Wall -Wextra -Wreturn-local-addr -Werror=format -Werror=return-local-addr
  -DBOARD_HAS_PSRAM -mfix-esp32-psram-cache-issue

;----
[env:release]
board = esp32s3box
board_build.arduino.memory_type = qio_qspi
upload_speed = 1500000
build_type=release
build_flags=${env.build_flags} ${option_release.build_flags}
```

PSRAM-critical flags: **`-DBOARD_HAS_PSRAM -mfix-esp32-psram-cache-issue`** plus
`board_build.arduino.memory_type = qio_qspi`. Note GOB52 uses `board = esp32s3box` (NOT a cores3
board id) — your repo's `cores3-stackchan` env already uses a working CoreS3 board config; keep
that and just add the PSRAM flags + lib_deps. M5Stack's own CoreS3 PlatformIO snippet also adds
`-DARDUINO_USB_CDC_ON_BOOT=1 -DARDUINO_USB_MODE=1`.

### 3b. GPIO conflicts on CoreS3 — the real gotchas

| Pin(s) | Camera use | Conflicts with |
|---|---|---|
| **GPIO11 / GPIO12** | SCCB SCL / SDA | **CoreS3 internal system I2C** (`I2C_SYS`) — BMI270, AXP2101, BM8563, ES7210, AW88298, **AW9523B**, touch FT6336. This is the big one. |
| GPIO2 | XCLK | (LEDC-generated; verify nothing else claims GPIO2) |
| GPIO39-42, 15, 16, 47, 48 | DVP D0-D7 | dedicated camera data lines on CoreS3 |
| GPIO38, 45, 46 | HREF, PCLK, VSYNC | dedicated |

**The GPIO11/12 conflict is the critical one.** GC0308 (0x21) and the LTR-553 proximity sensor (0x23)
sit on the *same physical I2C bus as every internal CoreS3 chip* (AXP2101 PMIC, AW9523B expander, BM8563
RTC, BMI270 IMU, ES7210 ADC, AW88298 amp, FT6336 touch). GOB52's solution (verbatim): call
`M5.In_I2C.release()` *before* `esp_camera_init()` and let esp32-camera re-own GPIO11/12 as its private
SCCB bus (`sccb_i2c_port = -1`). The downside: while the camera owns the bus, M5Unified can't talk to
the IMU/RTC/touch/PMIC. The alternative (`#define USING_EXISTING_I2C`) keeps M5's bus and passes
`sccb_i2c_port = M5.In_I2C.getPort()` so the camera shares it — but that `#if` branch in upstream
`main.cpp` is written with **semicolons instead of commas** in the struct initializer (i.e. it does
not compile as-is) and is clearly the un-maintained path. **Use the `#else` / `M5.In_I2C.release()`
path that upstream actually ships.**

For your StackChan firmware: releasing the internal I2C bus means losing M5Unified access to touch,
RTC, IMU, and the AW88298 speaker amp control *while the camera is active*. The speaker (`sound.cpp`)
and any servo/LED work that goes through M5Unified I2C will be affected. You either (a) init+configure
all I2C peripherals before `M5.In_I2C.release()` + camera init and accept no further runtime I2C, or
(b) use the shared-port approach (fix the comma bug yourself) so M5 keeps the bus — at the cost of
SCCB contention. The DVP data pins (39-42,15,16,47,48 + 38,45,46,2) are camera-dedicated on CoreS3 and
do not collide with LCD/speaker/mic.

### 3c. Brownout

Not addressed in GOB52's source. Standard ESP32-S3 camera brownout mitigation if you hit it:
camera + WiFi + LCD + servos on USB-only power is the risk. The repo relies on the AXP2101 PMIC
(brought up by `M5.begin()`) for rail management. If brownouts appear, the usual escape hatch is
disabling the brownout detector (`WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0)`), but that's a
workaround, not upstream-blessed — better to budget power (lower `fb_count`, lower XCLK, throttle
servos during capture, as your StackChan motion.cpp comments already note USB-budget tuning).
