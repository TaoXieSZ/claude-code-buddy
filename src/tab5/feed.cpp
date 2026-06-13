// M1 live feed — NDJSON heartbeat over USB-CDC serial (REFERENCE.md schema).
// The cc-bridge daemon writes the same snapshot it sends the BLE sticks;
// we parse it into the dashboard session and answer permission prompts with
// {"cmd":"permission","id":...,"decision":"once"|"deny"} lines.
#include <Arduino.h>
#include <ArduinoJson.h>
#include "mbedtls/base64.h"
#include "ui.h"
#include "sound.h"

static char     g_buf[4608];
static size_t   g_len = 0;
static char     g_lastEntry[224] = "";
static uint32_t g_doneUntil = 0;
static int      g_prevRunning = 0;

static void handleHeartbeat(JsonDocument& doc, int sess) {
  // One-shot sound trigger — daemon sets "play" for exactly one heartbeat
  // per hook event (same contract as the stackchan firmware).
  const char* play = doc["play"] | (const char*)nullptr;
  if (play && play[0]) soundPlay(play);

  int running = doc["running"] | 0;
  int total   = doc["total"] | 0;
  uint32_t tokens = doc["tokens"] | 0;
  const char* msg = doc["msg"] | "";

  JsonVariant prompt = doc["prompt"];
  bool pending = !prompt.isNull();
  if (pending) {
    const char* id   = prompt["id"]   | "";
    const char* tool = prompt["tool"] | "";
    const char* hint = prompt["hint"] | "";
    char text[96];
    snprintf(text, sizeof(text), "%s  %s", tool, hint);
    uiFeedPrompt(sess, true, id, text);
  } else {
    uiFeedPrompt(sess, false, "", "");
  }

  // celebrate briefly when the last running session finishes
  if (g_prevRunning > 0 && running == 0 && !pending) g_doneUntil = millis() + 3000;
  g_prevRunning = running;

  uint8_t st;
  if (pending)                    st = 2;   // ST_ATTN
  else if (running > 0)           st = 1;   // ST_BUSY
  else if (millis() < g_doneUntil) st = 3;  // ST_DONE
  else                            st = 0;   // ST_IDLE
  uiFeedState(sess, st, (total == 0) ? "no sessions" : msg, tokens);

  // entries[] is newest-first; append only what's new since the last beat.
  // (Compare against the stashed previous newest — the data.h lineGen lesson:
  // comparing against a mutating field re-fires every tick.)
  JsonArray entries = doc["entries"];
  if (!entries.isNull() && entries.size() > 0) {
    int n = entries.size();
    int newCount = n;                        // default: all are new
    for (int i = 0; i < n; i++) {
      const char* e = entries[i] | "";
      if (strncmp(e, g_lastEntry, sizeof(g_lastEntry) - 1) == 0) { newCount = i; break; }
    }
    for (int i = newCount - 1; i >= 0; i--) {   // oldest→newest
      const char* e = entries[i] | "";
      if (e[0]) uiFeedLine(sess, e);
    }
    snprintf(g_lastEntry, sizeof(g_lastEntry), "%s", (const char*)(entries[0] | ""));
  }
}

static void handleLine(const char* line) {
  if (line[0] != '{') return;
  JsonDocument doc;
  DeserializationError jerr = deserializeJson(doc, line);
  if (jerr != DeserializationError::Ok) {
    Serial.printf("[feed] json parse failed (%s) len=%d\n", jerr.c_str(), (int)strlen(line));
    return;
  }
  // control command: screenshot request (dev tool)
  const char* cmd = doc["cmd"] | (const char*)nullptr;
  if (cmd && !strcmp(cmd, "shot")) { uiScreenshot(); return; }

  // a heartbeat is recognized by its mandatory counters
  if (!doc["running"].isNull() || !doc["entries"].isNull()) {
    // route by source tag: "cursor" → session 1, anything else → session 0
    const char* app = doc["app"] | "claude";
    int sess = (strcmp(app, "cursor") == 0) ? 1 : 0;
    uiFeedAlive(sess);
    handleHeartbeat(doc, sess);
    Serial.printf("[feed] hb ok app=%s running=%d msg=%s\n",
                  app, (int)(doc["running"] | 0), (const char*)(doc["msg"] | ""));
  } else {
    Serial.println("[feed] json line ignored (no heartbeat keys)");
  }
}

void feedSendMic(bool down) {
  // Same wire command the sticks send; the daemon maps it to the Mac
  // dictation hotkey per *_BRIDGE_PTT_MODE.
  Serial.printf("{\"cmd\":\"mic\",\"state\":\"%s\"}\n", down ? "down" : "up");
}

void feedSendAudio(const int16_t* pcm, int n) {
  // `A<base64>` frame of raw S16LE mono PCM — the daemon plays it into the
  // BlackHole device so the Mac dictation app hears the Tab5 mic.
  static uint8_t b64[1400];
  size_t olen = 0;
  if (mbedtls_base64_encode(b64, sizeof(b64), &olen,
                            (const uint8_t*)pcm, n * 2) != 0) return;
  Serial.print('A');
  Serial.write(b64, olen);
  Serial.print('\n');
}

// ── keyboard relay (Tab5 → Mac second keyboard) ──────────────────────
// HID usage code → name for non-printable keys.
static const char* keySpecialName(uint8_t u) {
  switch (u) {
    case 0x28: return "enter";
    case 0x2A: return "backspace";
    case 0x2B: return "tab";
    case 0x29: return "esc";
    case 0x4F: return "right";
    case 0x50: return "left";
    case 0x51: return "down";
    case 0x52: return "up";
    case 0x4C: return "fwddelete";
    default:   return nullptr;
  }
}
// HID usage → base (unshifted) printable char, or 0.
static char keyBaseChar(uint8_t u) {
  if (u >= 0x04 && u <= 0x1D) return 'a' + (u - 0x04);   // a-z
  if (u >= 0x1E && u <= 0x26) return '1' + (u - 0x1E);   // 1-9
  if (u == 0x27) return '0';
  switch (u) {
    case 0x2C: return ' ';
    case 0x2D: return '-';  case 0x2E: return '=';
    case 0x2F: return '[';  case 0x30: return ']';  case 0x31: return '\\';
    case 0x33: return ';';  case 0x34: return '\'';
    case 0x35: return '`';  case 0x36: return ',';   case 0x37: return '.';
    case 0x38: return '/';
    default:   return 0;
  }
}
// HID usage → shifted printable char, or 0.
static char keyShiftChar(uint8_t u) {
  if (u >= 0x04 && u <= 0x1D) return 'A' + (u - 0x04);   // A-Z
  switch (u) {
    case 0x1E: return '!'; case 0x1F: return '@'; case 0x20: return '#';
    case 0x21: return '$'; case 0x22: return '%'; case 0x23: return '^';
    case 0x24: return '&'; case 0x25: return '*'; case 0x26: return '(';
    case 0x27: return ')';
    case 0x2D: return '_'; case 0x2E: return '+';
    case 0x2F: return '{'; case 0x30: return '}'; case 0x31: return '|';
    case 0x33: return ':'; case 0x34: return '"';
    case 0x35: return '~'; case 0x36: return '<'; case 0x37: return '>';
    case 0x38: return '?';
    default:   return 0;
  }
}
// Build a JSON mods array from the HID modifier byte. `withShift` includes
// "shift" (used for special keys / shortcuts, not for plain printables where
// shift is already baked into the character).
static void keyMods(uint8_t mods, bool withShift, char* out, size_t cap) {
  bool ctrl = mods & 0x11, shift = mods & 0x22, alt = mods & 0x44, gui = mods & 0x88;
  int p = snprintf(out, cap, "[");
  bool first = true;
  if (gui)  { p += snprintf(out + p, cap - p, "%s\"cmd\"",  first ? "" : ","); first = false; }
  if (alt)  { p += snprintf(out + p, cap - p, "%s\"opt\"",  first ? "" : ","); first = false; }
  if (ctrl) { p += snprintf(out + p, cap - p, "%s\"ctrl\"", first ? "" : ","); first = false; }
  if (withShift && shift) { p += snprintf(out + p, cap - p, "%s\"shift\"", first ? "" : ","); first = false; }
  snprintf(out + p, cap - p, "]");
}

void feedSendKey(uint8_t hidKey, uint8_t mods) {
  bool shift = mods & 0x22;
  bool cmdLike = mods & (0x11 | 0x44 | 0x88);   // ctrl / alt / gui → a shortcut
  char modbuf[48];

  const char* sp = keySpecialName(hidKey);
  if (sp) {                                     // special key (+ any mods)
    keyMods(mods, /*withShift=*/true, modbuf, sizeof(modbuf));
    Serial.printf("{\"cmd\":\"key\",\"key\":\"%s\",\"mods\":%s}\n", sp, modbuf);
    return;
  }
  char b = keyBaseChar(hidKey);
  if (!b) return;                               // unmapped usage

  bool letterOrDigit = (b >= 'a' && b <= 'z') || (b >= '0' && b <= '9');
  if (cmdLike && letterOrDigit) {               // shortcut: kVK name + mods
    keyMods(mods, /*withShift=*/true, modbuf, sizeof(modbuf));
    Serial.printf("{\"cmd\":\"key\",\"key\":\"%c\",\"mods\":%s}\n", b, modbuf);
    return;
  }
  // plain printable: bake shift into the char, type via Unicode
  char c = (shift && keyShiftChar(hidKey)) ? keyShiftChar(hidKey) : b;
  if (c == '"' || c == '\\')
    Serial.printf("{\"cmd\":\"key\",\"ch\":\"\\%c\"}\n", c);
  else
    Serial.printf("{\"cmd\":\"key\",\"ch\":\"%c\"}\n", c);
}

void feedPoll() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      g_buf[g_len] = 0;
      if (g_len) handleLine(g_buf);
      g_len = 0;
    } else if (g_len < sizeof(g_buf) - 1) {
      g_buf[g_len++] = c;
    } else {
      g_len = 0;   // oversized line — drop and resync
    }
  }

  // pump touch verdicts back to the daemon — tagged with the source app so
  // a multiplexing hub can route the ack to the originating bridge
  char id[48]; char app[16]; bool allow;
  while (uiTakeDecision(id, sizeof(id), &allow, app, sizeof(app))) {
    Serial.printf("{\"cmd\":\"permission\",\"app\":\"%s\",\"id\":\"%s\",\"decision\":\"%s\"}\n",
                  app, id, allow ? "once" : "deny");
  }
}
