// Tab5 agent-buddy firmware — P1 design pass.
// Boot, join WiFi (ESP-Hosted → C6), feed live status (wifi/battery/mic)
// into the dashboard UI (ui.cpp). Sessions/transcript are DEMO data until
// M1 wires the TCP feeds.
//
// NOTE on the C6 co-processor firmware: the factory C6 ships esp-hosted
// slave 1.4.1, which the Arduino host stack (esp-hosted 2.8.x) cannot talk
// to — scans return 0/-2 and the in-firmware hosted OTA API can't reach it
// either (the 2.x OTA RPC times out against a 1.x slave). The one-shot
// ESP-IDF updater at tools/tab5-c6-updater/ (esp_hosted 1.4.0 host = the
// factory protocol generation) flashes esp32c6-v2.8.5 over SDIO; run it
// once per new unit. Do NOT re-add a hostedHasUpdate()-driven self-update
// here with a baked-in image: once the host stack moves past the embedded
// version it would reflash the stale image on every boot.
#include <M5Unified.h>
#include <WiFi.h>
#include "ui.h"
#include "sound.h"

static int16_t micBuf[256];
static int micPeak = 0;

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  // Heartbeats with transcript entries run >1KB per line; the HWCDC default
  // 256-byte RX ring overflows mid-burst and every oversized snapshot is
  // silently corrupted. Size it before begin().
  Serial.setRxBufferSize(8192);
  Serial.begin(115200);
  // The M5GFX develop driver intermittently fails panel init (width()==0,
  // "ST touch FW version read failed") — retry a few times before giving up.
  for (int tries = 0; M5.Display.width() == 0 && tries < 3; tries++) {
    Serial.printf("[tab5] display init failed — retry %d\n", tries + 1);
    delay(250);
    M5.Display.init();
  }
  M5.Display.setRotation(3);  // landscape 1280x720 (1 is upside-down on this unit)
  M5.Display.setBrightness(255);   // always wake bright after boot/flash

  M5.Mic.begin();

  // Tab5's P4↔C6 SDIO wiring — MUST be set before the first WiFi call (the
  // m5stack_tab5 variant carries these only on newer cores; explicit = safe).
  WiFi.setPins(/*clk*/12, /*cmd*/13, /*d0*/11, /*d1*/10, /*d2*/9, /*d3*/8, /*rst*/15);
  WiFi.mode(WIFI_STA);
  WiFi.begin(TAB5_WIFI_SSID, TAB5_WIFI_PASS);
  Serial.printf("[tab5] wifi: joining %s ...\n", TAB5_WIFI_SSID);

  uiInit();
  soundInit();   // after uiInit — LittleFS is mounted there
  kbdInit();   // USB-A HID keyboard host
  Serial.printf("[tab5] board=%d display=%dx%d psram=%u mic=%d\n",
                (int)M5.getBoard(), M5.Display.width(), M5.Display.height(),
                (unsigned)ESP.getPsramSize(), (int)M5.Mic.isEnabled());
}

void loop() {
  M5.update();
  feedPoll();   // drain cc-bridge heartbeats + push touch verdicts
  kbdPoll();    // decoded USB keyboard keys → UI
  soundTick();  // hand I2S back to the mic when a clip finishes

  // While push-to-talk is held, capture 16 kHz mono in 20 ms chunks and stream
  // them to the daemon (→ BlackHole). Ping-pong two buffers so playback is
  // gapless: when record() accepts a new buffer, the other one just finished.
  static int16_t aud[2][320];
  static int  aIdx = 0;
  static bool aPrimed = false;
  if (uiMicHeld()) {
    if (M5.Mic.record(aud[aIdx], 320, 16000)) {
      if (aPrimed) {
        const int16_t* done = aud[aIdx ^ 1];
        feedSendAudio(done, 320);
        int peak = 0;
        for (int i = 0; i < 320; i++) { int a = done[i] < 0 ? -done[i] : done[i]; if (a > peak) peak = a; }
        micPeak = peak > micPeak ? peak : micPeak - (micPeak >> 3);
      }
      aPrimed = true;
      aIdx ^= 1;
    }
  } else {
    aPrimed = false;
    if (M5.Mic.record(micBuf, 256, 16000)) {
      int peak = 0;
      for (auto s : micBuf) { int a = s < 0 ? -s : s; if (a > peak) peak = a; }
      micPeak = peak > micPeak ? peak : micPeak - (micPeak >> 3);
    }
  }

  // Power policy: never blank — dim to 25% after 2 min without touch,
  // restore instantly on touch. (User preference: always-visible desk display.)
  static uint32_t lastInteract = 0;
  static uint8_t  curBright = 255;
  if (M5.Touch.getCount() > 0) lastInteract = millis();
  uint8_t wantBright = (millis() - lastInteract > 120000) ? 64 : 255;
  if (wantBright != curBright) { curBright = wantBright; M5.Display.setBrightness(curBright); }

  UiStatus st;
  st.wifiUp = (WiFi.status() == WL_CONNECTED);
  if (st.wifiUp) snprintf(st.ip, sizeof(st.ip), "%s", WiFi.localIP().toString().c_str());
  else st.ip[0] = 0;
  st.rssi = st.wifiUp ? WiFi.RSSI() : 0;
  // PMIC reads occasionally return 0/100 outliers (worse when hammered —
  // this used to poll every loop tick). Sample every 2s, report the median
  // of the last three so a single bad read never reaches the UI.
  static int  battS[3] = {-1, -1, -1};
  static int  battIdx = 0, battShown = 100;
  static uint32_t battAt = 0;
  if (millis() - battAt >= 2000) {
    battAt = millis();
    int b = M5.Power.getBatteryLevel();
    battS[battIdx++ % 3] = b < 0 ? 0 : (b > 100 ? 100 : b);
    if (battS[0] >= 0 && battS[1] >= 0 && battS[2] >= 0) {
      int lo = min(battS[0], min(battS[1], battS[2]));
      int hi = max(battS[0], max(battS[1], battS[2]));
      battShown = battS[0] + battS[1] + battS[2] - lo - hi;
    } else if (battS[0] >= 0) {
      battShown = battS[0];
    }
  }
  st.battPct = battShown;
  st.micLevel = micPeak * 100 / 32767;

  uiTick(st);

  static uint32_t lastLog = 0;
  if (millis() - lastLog >= 5000) {
    lastLog = millis();
    Serial.printf("[tab5] up=%lus wifi=%d ip=%s rssi=%d batt=%d mic=%d\n",
                  (unsigned long)(millis() / 1000), (int)WiFi.status(),
                  st.wifiUp ? st.ip : "-", st.rssi, st.battPct, st.micLevel);
  }
}
