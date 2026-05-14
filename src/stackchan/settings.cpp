// settings.cpp — NVS-backed runtime settings for StackChan buddy.
//
// Owns the in-RAM copy; setters write-through to NVS via the Preferences
// library (key-value store inside the ESP32 NVS partition). Sub-systems
// (sound / motion / character) expose apply hooks that the setters call
// after persisting.

#include "settings.h"
#include "motion.h"
#include "character_chan.h"
#include <M5Unified.h>
#include <Preferences.h>
#include <string.h>

namespace {

Preferences g_nvs;
constexpr const char* NS = "stackbuddy";

uint8_t g_volume      = 96;
uint8_t g_brightness  = 200;
char    g_char_name[24] = "";
bool    g_motion      = true;
bool    g_idle_wiggle = true;
uint8_t g_tilt        = 65;   // degrees, 0..90; servo Y baseline

}  // namespace

void settingsInit() {
  if (!g_nvs.begin(NS, /*readOnly=*/false)) {
    Serial.println("[set] NVS open failed; using defaults");
    return;
  }
  g_volume     = g_nvs.getUChar("vol",    g_volume);
  g_brightness = g_nvs.getUChar("bright", g_brightness);
  g_motion     = g_nvs.getBool ("motion", g_motion);
  g_idle_wiggle= g_nvs.getBool ("idlew",  g_idle_wiggle);
  g_tilt       = g_nvs.getUChar("tilt",   g_tilt);
  if (g_tilt > 90) g_tilt = 90;
  String cn    = g_nvs.getString("char",  "");
  size_t cl    = cn.length();
  if (cl > 0) {
    if (cl >= sizeof(g_char_name)) cl = sizeof(g_char_name) - 1;
    memcpy(g_char_name, cn.c_str(), cl);
    g_char_name[cl] = 0;
  }
  Serial.printf("[set] loaded: vol=%u bright=%u motion=%d idlew=%d tilt=%u char='%s'\n",
                g_volume, g_brightness, g_motion, g_idle_wiggle, g_tilt, g_char_name);

  // Apply baseline ASAP so boot UI matches saved state. tilt is applied
  // later by main.cpp after motionInit (servo subsystem must be up first).
  M5.Speaker.setVolume(g_volume);
  M5.Lcd.setBrightness(g_brightness);
}

uint8_t  settingsGetVolume()        { return g_volume; }
uint8_t  settingsGetBrightness()    { return g_brightness; }
const char* settingsGetCharName()   { return g_char_name; }
bool     settingsGetMotionEnabled() { return g_motion; }
bool     settingsGetIdleWiggleEnabled() { return g_idle_wiggle; }
uint8_t  settingsGetTilt()          { return g_tilt; }

void settingsSetVolume(uint8_t v) {
  g_volume = v;
  g_nvs.putUChar("vol", v);
  M5.Speaker.setVolume(v);
  Serial.printf("[set] vol=%u\n", v);
}

void settingsSetBrightness(uint8_t v) {
  g_brightness = v;
  g_nvs.putUChar("bright", v);
  M5.Lcd.setBrightness(v);
  Serial.printf("[set] bright=%u\n", v);
}

void settingsSetCharName(const char* name) {
  if (!name) name = "";
  strncpy(g_char_name, name, sizeof(g_char_name) - 1);
  g_char_name[sizeof(g_char_name) - 1] = 0;
  g_nvs.putString("char", g_char_name);
  Serial.printf("[set] char='%s'\n", g_char_name);
  // Trigger reload — characterReload re-runs init with the new pack
  // and forces SLEEP to repaint with new file paths.
  characterReload(g_char_name[0] ? g_char_name : nullptr);
}

void settingsSetMotionEnabled(bool on) {
  g_motion = on;
  g_nvs.putBool("motion", on);
  motionSetEnabled(on);
  Serial.printf("[set] motion=%d\n", on);
}

void settingsSetIdleWiggleEnabled(bool on) {
  g_idle_wiggle = on;
  g_nvs.putBool("idlew", on);
  motionSetIdleWiggle(on);
  Serial.printf("[set] idlew=%d\n", on);
}

void settingsSetTilt(uint8_t deg) {
  if (deg > 90) deg = 90;
  g_tilt = deg;
  g_nvs.putUChar("tilt", deg);
  motionSetTilt(deg);
  Serial.printf("[set] tilt=%u\n", deg);
}
