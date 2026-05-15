// GC0308 camera bring-up + capture (CoreS3 / StackChan).
//
// Init sequence is COPIED VERBATIM from pinned upstream:
//   GOB52/M5StackCoreS3_CameraWebServer @ 58989c64
//   GOB52/gob_GC0308 @ a488fc63
// See openspec/changes/2026-05-15-0003-stackchan-camera-gestures/
//   cores3-camera-upstream-reference.md
//
// Project discipline: any deviation from the upstream sequence MUST be
// commented with why. The known-correct path is:
//   1. M5.begin()                  (done in main.cpp setup)
//   2. M5.In_I2C.release()         (frees GPIO11/12 for SCCB)
//   3. esp_camera_init(&config)
//   4. goblib::camera::GC0308::complementDriver()   (exactly once)
// No manual AW9523B / P1_0 / camera-reset code — M5.begin() owns it.

#include "camera_chan.h"

#include <M5Unified.h>
#include <esp_camera.h>
#include <gob_GC0308.hpp>

// Pin assignments — verbatim from upstream main.cpp. CoreS3-specific.
static camera_config_t s_camera_config = {
    .pin_pwdn      = -1,
    .pin_reset     = -1,
    .pin_xclk      = 2,
    .pin_sscb_sda  = 12,
    .pin_sscb_scl  = 11,
    .pin_d7        = 47,
    .pin_d6        = 48,
    .pin_d5        = 16,
    .pin_d4        = 15,
    .pin_d3        = 42,
    .pin_d2        = 41,
    .pin_d1        = 40,
    .pin_d0        = 39,
    .pin_vsync     = 46,
    .pin_href      = 38,
    .pin_pclk      = 45,
    .xclk_freq_hz  = 20000000,
    .ledc_timer    = LEDC_TIMER_0,
    .ledc_channel  = LEDC_CHANNEL_0,
    .pixel_format  = PIXFORMAT_RGB565,   // GC0308 has no JPEG hardware
    .frame_size    = FRAMESIZE_QVGA,     // 320x240 — daemon target
    .jpeg_quality  = 0,                  // unused for RGB565
    .fb_count      = 2,
    .fb_location   = CAMERA_FB_IN_PSRAM,
    .grab_mode     = CAMERA_GRAB_WHEN_EMPTY,
    .sccb_i2c_port = -1,                 // re-own GPIO11/12 privately
};

static bool s_active = false;

bool cameraStart() {
    if (s_active) return true;

    // Release M5Unified's hold on the internal I2C bus so esp32-camera can
    // re-own GPIO11/12 for its private SCCB. While this is in effect:
    //   - AW88298 speaker amp control (sound.cpp): UNAVAILABLE
    //   - FT6336 touch / BM8563 RTC / BMI270 IMU: UNAVAILABLE
    //   - LEDC servo PWM (motion.cpp): unaffected (separate peripheral)
    //   - SPI LCD (character_chan.cpp): unaffected
    M5.In_I2C.release();

    esp_err_t err = esp_camera_init(&s_camera_config);
    if (err != ESP_OK) {
        M5_LOGE("cameraStart: esp_camera_init failed (0x%x)", err);
        // Re-acquire so the rest of the firmware stays usable.
        M5.In_I2C.begin();
        return false;
    }

    if (!goblib::camera::GC0308::complementDriver()) {
        M5_LOGE("cameraStart: GC0308 complementDriver failed");
        esp_camera_deinit();
        M5.In_I2C.begin();
        return false;
    }

    s_active = true;
    M5_LOGI("cameraStart: ok (QVGA RGB565, sound disabled)");
    return true;
}

void cameraStop() {
    if (!s_active) return;
    esp_camera_deinit();
    // Re-acquire the M5 internal I2C bus so sound / touch / RTC / IMU work
    // again. M5Unified's In_I2C.begin() is the symmetric counterpart to
    // .release(). If on-device the speaker doesn't come back after the first
    // prompt teardown, this is where to look — try M5.Speaker.begin() or
    // M5.begin(cfg) re-issue.
    M5.In_I2C.begin();
    s_active = false;
    M5_LOGI("cameraStop: deinit + I2C re-acquired");
}

bool cameraIsActive() { return s_active; }

bool cameraCaptureJpeg(uint8_t** out_buf, size_t* out_len) {
    if (!s_active || !out_buf || !out_len) return false;

    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
        M5_LOGE("cameraCaptureJpeg: fb_get failed");
        return false;
    }

    // GC0308 is always PIXFORMAT_RGB565 — frame2jpg is always taken.
    // frame2jpg quality 80 matches upstream MJPEG handler. The output
    // buffer is owned by the caller (must free()).
    bool ok = frame2jpg(fb, 80, out_buf, out_len);
    esp_camera_fb_return(fb);

    if (!ok) {
        M5_LOGE("cameraCaptureJpeg: frame2jpg failed");
        return false;
    }
    return true;
}
