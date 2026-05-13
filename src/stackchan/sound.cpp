// sound.cpp — pre-loaded WAV clip playback for StackChan.
//
// Dynamically enumerates /sounds/*.wav on LittleFS at boot and loads
// each into a PSRAM buffer indexed by basename (lowercase, no .wav).
// Daemon sends `{"play": "<name>"}` and firmware looks up the buffer.
//
// Earlier version had a hardcoded 2-clip table — adding hook events
// required firmware rebuilds. Dynamic loading means new sounds just
// need a `uploadfs` of data/sounds/<eventname>.wav.

#include "sound.h"
#include <M5Unified.h>
#include <LittleFS.h>
#include <string.h>
#include <strings.h>   // strcasecmp

namespace {

struct Clip {
  char     name[28];   // basename without .wav, lowercase
  uint8_t* buf;
  size_t   len;
};

constexpr size_t MAX_CLIPS = 64;
Clip   g_clips[MAX_CLIPS];
size_t g_n_clips = 0;

bool loadOne(const char* path, const char* fname) {
  if (g_n_clips >= MAX_CLIPS) {
    Serial.printf("[snd] MAX_CLIPS reached, skipping %s\n", path);
    return false;
  }
  File f = LittleFS.open(path, "r");
  if (!f) return false;
  size_t sz = f.size();
  uint8_t* buf = (uint8_t*)ps_malloc(sz);
  if (!buf) {
    Serial.printf("[snd] ps_malloc failed for %s (%u bytes)\n",
                  path, (unsigned)sz);
    f.close();
    return false;
  }
  if (f.read(buf, sz) != sz) {
    Serial.printf("[snd] short read on %s\n", path);
    free(buf);
    f.close();
    return false;
  }
  f.close();

  Clip& c = g_clips[g_n_clips];
  // Strip ".wav" suffix; lowercase the name for case-insensitive lookup.
  size_t fl = strlen(fname);
  size_t copy = fl > sizeof(c.name) - 1 ? sizeof(c.name) - 1 : fl;
  memcpy(c.name, fname, copy);
  c.name[copy] = 0;
  if (copy >= 4 && strcasecmp(c.name + copy - 4, ".wav") == 0) {
    c.name[copy - 4] = 0;
  }
  for (char* p = c.name; *p; p++) {
    if (*p >= 'A' && *p <= 'Z') *p += 32;
  }
  c.buf = buf;
  c.len = sz;
  g_n_clips++;
  Serial.printf("[snd] %-28s %u bytes\n", c.name, (unsigned)sz);
  return true;
}

}  // namespace

void soundInit() {
  // M5.Speaker is initialized by M5.begin() for CoreS3 (AXP-managed
  // amplifier wakes automatically). Volume range 0-255; 96 is audible
  // without being shrill at desk distance.
  M5.Speaker.setVolume(96);

  File root = LittleFS.open("/sounds");
  if (!root || !root.isDirectory()) {
    Serial.println("[snd] /sounds not found on LittleFS");
    return;
  }

  File e = root.openNextFile();
  while (e) {
    if (!e.isDirectory()) {
      const char* full = e.name();
      const char* slash = strrchr(full, '/');
      const char* base = slash ? slash + 1 : full;
      size_t bl = strlen(base);
      if (bl > 4 && strcasecmp(base + bl - 4, ".wav") == 0) {
        char path[80];
        snprintf(path, sizeof(path), "/sounds/%s", base);
        loadOne(path, base);
      }
    }
    e = root.openNextFile();
  }
  Serial.printf("[snd] loaded %u clips\n", (unsigned)g_n_clips);
}

void soundPlay(const char* name) {
  if (!name || !*name) return;
  // Compare case-insensitively against stored lowercase names.
  for (size_t i = 0; i < g_n_clips; i++) {
    if (strcasecmp(g_clips[i].name, name) == 0) {
      bool ok = M5.Speaker.playWav(g_clips[i].buf, g_clips[i].len,
                                   /*repeat=*/1, /*channel=*/-1,
                                   /*stop_current_sound=*/true);
      Serial.printf("[snd] play %s (%u B) -> %s\n",
                    g_clips[i].name, (unsigned)g_clips[i].len,
                    ok ? "ok" : "FAIL");
      return;
    }
  }
  Serial.printf("[snd] unknown clip: %s\n", name);
}
