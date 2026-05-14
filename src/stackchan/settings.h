#pragma once
#include <stdint.h>

// Persistent runtime settings for StackChan buddy, mirrored on NVS so
// dashboard changes survive reboots. All getters return the current
// (in-RAM) value; setters update RAM, save to NVS, and call into the
// owning subsystem (sound / motion / lcd / character) to apply.
//
// Default values match the hardcoded behaviour from the initial firmware:
//   volume      = 96   (M5.Speaker volume 0-255)
//   brightness  = 200  (M5.Lcd brightness 0-255)
//   char_name   = ""   (autodetect — falls back to BUDDY_DEFAULT_CHAR)
//   motion      = true
//   idle_wiggle = true

void     settingsInit();                // call after M5.begin + LittleFS mount

uint8_t  settingsGetVolume();
void     settingsSetVolume(uint8_t v);

uint8_t  settingsGetBrightness();
void     settingsSetBrightness(uint8_t v);

const char* settingsGetCharName();      // empty string if unset
void        settingsSetCharName(const char* name);

bool     settingsGetMotionEnabled();
void     settingsSetMotionEnabled(bool on);

bool     settingsGetIdleWiggleEnabled();
void     settingsSetIdleWiggleEnabled(bool on);

// Head-up tilt in degrees (0..90). Default 65 (~head-up but well clear
// of the mechanical stop). Persisted to NVS, applied via motionSetTilt.
uint8_t  settingsGetTilt();
void     settingsSetTilt(uint8_t deg);
