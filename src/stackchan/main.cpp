// claude-desktop-buddy — StackChan (CoreS3) firmware
//
// MVP target: be a face-only display endpoint for the existing cc-bridge /
// cursor-bridge daemon. Reuses the **debug NUS** GATT service verbatim
// (UUIDs b0c2dbe6-cc0[1-3]-…), advertises as "Claude-SC-XXXX" so the
// daemon picks it up by its existing "Claude-" prefix scan — zero daemon
// changes needed. Plus2 must be unplugged or BLE-off while testing
// because daemon is single-peer for now.
//
// What this firmware does:
//   - Spin up an m5avatar face on CoreS3's 320x240 LCD.
//   - Run a BLE peripheral exposing both the encrypted NUS (claimed for
//     compatibility) and the debug NUS service the daemon actually uses.
//   - Buffer NUS RX into lines, JSON-parse each line, map the "msg" /
//     "prompt" / "running" fields onto an Avatar Expression.
//   - Emit a 10s keepalive ack so the daemon's BleWriter doesn't time out.
//
// Out of scope for MVP (next iterations, see CLAUDE.md plan):
//   - Servo wagging on lineGen bump
//   - 12 RGB LED status colors
//   - Body touch-zone PTT → keystroke
//   - NFC / IR
//   - Multi-peer daemon so Plus2 + StackChan can co-exist
//
// Build:  pio run -e cores3-stackchan-claude -t upload
// Listen: pio device monitor -e cores3-stackchan-claude

#include <M5Unified.h>
#include <ArduinoJson.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include "character_chan.h"
#include "sound.h"
#include "motion.h"
#include "settings.h"

// 2026-05-13: m5avatar dropped in favor of the Plus2 Clawd/Calico GIFs —
// user wants visual continuity with the stick UX, and character_chan.cpp
// already had the GIF pipeline working on M5Unified.

// ---- Branding (overridable from platformio.ini build_flags) ---------------
#ifndef BUDDY_BRAND_PREFIX
#define BUDDY_BRAND_PREFIX "Claude-SC-"
#endif
#ifndef BUDDY_BRAND_NAME
#define BUDDY_BRAND_NAME "Claude"
#endif

// ---- NUS UUIDs — must match daemon (buddy_core/core.py) -------------------
// Daemon uses the DEBUG service (b0c2dbe6-*). We expose both so a Claude
// Desktop scan that targets the standard NUS UUID also sees us.
#define NUS_SVC_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_RX_UUID  "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_TX_UUID  "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
#define DBG_SVC_UUID "b0c2dbe6-cc01-4000-8000-00805f9b34fb"
#define DBG_RX_UUID  "b0c2dbe6-cc02-4000-8000-00805f9b34fb"
#define DBG_TX_UUID  "b0c2dbe6-cc03-4000-8000-00805f9b34fb"

// ---- BLE state ------------------------------------------------------------
static BLEServer*         g_server = nullptr;
static BLECharacteristic* g_dbg_tx = nullptr;
static BLECharacteristic* g_nus_tx = nullptr;
static volatile bool      g_connected = false;

// Lock-free SPSC ring for inbound NUS bytes. Reason: the BLE controller
// task (BTC_TASK) has only ~3-4KB of stack. Earlier we parsed JSON +
// touched the LCD directly in onWrite — boom, "Stack canary watchpoint
// triggered (BTC_TASK)" panic every few seconds, looking like a flaky
// BLE link from the daemon's perspective. RX callback now only pushes
// bytes; loop() drains the ring and does all the heavy work (parse,
// state mutate, LCD repaint) on the Arduino app task's bigger stack.
constexpr size_t          RX_RING_SIZE = 4096;
static volatile char      g_rx_ring[RX_RING_SIZE];
static volatile size_t    g_rx_head = 0;   // written by BTC task
static volatile size_t    g_rx_tail = 0;   // read by app task

// ---- Character state ------------------------------------------------------
static uint8_t  g_cur_state   = CHAR_SLEEP;
static uint32_t g_last_rx_ms  = 0;
static uint32_t g_celebrate_until = 0;  // brief "done" celebrate timeout

// Map the daemon's status payload to a CharState. Priority:
//   permission-pending  → ATTENTION
//   approve: …          → ATTENTION
//   thinking… / running → BUSY
//   done: …             → CELEBRATE (briefly, then IDLE — handled in loop)
//   ready / running==0  → IDLE
//   stale (>20s no rx)  → SLEEP
static uint8_t mapState(const JsonDocument& doc, bool* out_done) {
  *out_done = false;
  if (!doc["prompt"].isNull()) return CHAR_ATTENTION;

  const char* msg = doc["msg"] | "";
  if (strstr(msg, "approve"))   return CHAR_ATTENTION;
  if (strstr(msg, "thinking"))  return CHAR_BUSY;
  if (strstr(msg, "running"))   return CHAR_BUSY;
  if (strstr(msg, "done"))    { *out_done = true; return CHAR_CELEBRATE; }

  int running = doc["running"] | 0;
  int waiting = doc["waiting"] | 0;
  if (waiting > 0) return CHAR_ATTENTION;
  if (running > 0) return CHAR_BUSY;
  return CHAR_IDLE;
}

static void applyJsonLine(const char* line) {
  if (!line || !*line) return;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    Serial.printf("[json] parse err: %s on line=%.40s\n", err.c_str(), line);
    return;
  }
  g_last_rx_ms = millis();

  // Soak up time sync silently so the RTC could be set later (deferred).
  if (!doc["time"].isNull()) return;

  // One-shot sound trigger. Daemon sets "play" in to_payload() and clears
  // it the same tick, so each heartbeat with "play" should fire exactly
  // once. No de-dup needed here — duplicate frames are rare and the
  // user-facing failure mode (one extra blip) is much better than
  // missing the sound entirely.
  const char* play = doc["play"] | (const char*)nullptr;
  if (play && *play) soundPlay(play);

  // Dashboard settings cmds — daemon forwards POSTs from the web UI as
  // {"cmd":"vol","v":N} / "bright" / "char" / "motion" / "idle_wiggle".
  // Each setter persists to NVS and applies immediately. Returning
  // after a settings cmd means the rest of applyJsonLine (state map,
  // status bar repaint) is skipped — keeps cmds idempotent and avoids
  // a spurious state flip from a settings frame that has no msg field.
  const char* cmd = doc["cmd"] | (const char*)nullptr;
  if (cmd && *cmd) {
    if (strcmp(cmd, "vol") == 0) {
      settingsSetVolume((uint8_t)(int)(doc["v"] | 96));
      return;
    }
    if (strcmp(cmd, "bright") == 0) {
      settingsSetBrightness((uint8_t)(int)(doc["v"] | 200));
      return;
    }
    if (strcmp(cmd, "char") == 0) {
      const char* n = doc["name"] | "";
      if (*n) settingsSetCharName(n);
      return;
    }
    if (strcmp(cmd, "motion") == 0) {
      settingsSetMotionEnabled(doc["enabled"] | true);
      return;
    }
    if (strcmp(cmd, "idle_wiggle") == 0) {
      settingsSetIdleWiggleEnabled(doc["enabled"] | true);
      return;
    }
  }

  bool is_done = false;
  uint8_t next = mapState(doc, &is_done);
  if (is_done) {
    // Hold celebrate ~1.5s so the wag is visible even when "done" fires
    // immediately after "running" — otherwise next msg overwrites it.
    g_celebrate_until = millis() + 1500;
  }
  if (next != g_cur_state) {
    g_cur_state = next;
    characterSetState(next);
    motionSetState(next);   // dance pattern mirrors visual state
    const char* msg = doc["msg"] | "";
    Serial.printf("[state] %u  msg=\"%s\"\n", next, msg);
  }

  // Bottom status bar — msg + stats.
  const char* msg = doc["msg"] | "";
  characterSetMsg(msg);

  int      running = doc["running"]      | 0;
  int      waiting = doc["waiting"]      | 0;
  uint32_t tokens  = doc["tokens_today"] | doc["tokens"] | 0;
  // Pull the tool name out of "running: <tool>" / "done: <tool>" so the
  // stats row gets short tool context. msg already shows the full form
  // on the larger row above; here we extract the after-colon tail.
  const char* tool = "";
  const char* colon = (msg && *msg) ? strchr(msg, ':') : nullptr;
  if (colon && colon[1] == ' ') tool = colon + 2;
  characterSetStats(running, waiting, tokens, tool);
}

// ---- BLE callbacks --------------------------------------------------------
class ServerCb : public BLEServerCallbacks {
  void onConnect(BLEServer*) override {
    g_connected = true;
    Serial.println("[ble] connected");
  }
  void onDisconnect(BLEServer*) override {
    g_connected = false;
    Serial.println("[ble] disconnected; re-advertising");
    BLEDevice::startAdvertising();
    // Daemon timed out -> back to idle face within ~20s via mapExpression.
  }
};

class RxCb : public BLECharacteristicCallbacks {
  // Runs on BTC_TASK (~3KB stack). Do NOTHING but push bytes — no
  // parsing, no LCD ops, no Serial.printf, no std::string allocation.
  // Earlier we used `std::string v = ch->getValue()` which heap-allocs
  // for any payload over the SSO threshold; combined with the precompiled
  // BT lib's already-deep call stack (~30 frames, 3.2KB used of a 4KB
  // BTC stack budget) the malloc internals tipped over the canary.
  // getData()+getLength() reads the BLE library's internal buffer
  // pointer directly — zero allocation, near-zero local stack.
  void onWrite(BLECharacteristic* ch) override {
    uint8_t* data = ch->getData();
    size_t   len  = ch->getLength();
    if (!data || len == 0) return;
    for (size_t i = 0; i < len; i++) {
      size_t next = (g_rx_head + 1) % RX_RING_SIZE;
      if (next == g_rx_tail) break;  // ring full → drop rest (caller will retry)
      g_rx_ring[g_rx_head] = (char)data[i];
      g_rx_head = next;
    }
  }
};

// Drain the SPSC ring on the app task, dispatch complete (newline-
// terminated) lines to applyJsonLine. The line buffer must hold the
// largest possible heartbeat: msg + 8 × ~120-char entries + counters
// + prompt object + play field → realistically 1.5-2.5KB. Earlier we
// had 1024 and the overflow path silently chopped the middle of each
// long line, leaving truncated JSON that parse-failed every time —
// which killed the `play` field dispatch (the user-visible symptom
// was "sound only fires after a daemon restart"). 4KB now; if even
// that's not enough, mark overflow and skip until next \n rather than
// resyncing mid-line.
static void drainRx() {
  static char line[4096];
  static size_t lpos = 0;
  static bool overflow = false;
  while (g_rx_tail != g_rx_head) {
    char c = g_rx_ring[g_rx_tail];
    g_rx_tail = (g_rx_tail + 1) % RX_RING_SIZE;
    if (c == '\n') {
      if (overflow) {
        Serial.printf("[rx] dropped oversized line (>%u bytes)\n",
                      (unsigned)sizeof(line));
        overflow = false;
      } else {
        line[lpos] = 0;
        applyJsonLine(line);
      }
      lpos = 0;
    } else if (overflow) {
      // Skip until next newline — discard everything in this line so we
      // don't pass a half-parsed buffer to ArduinoJson.
      continue;
    } else if (c != '\r') {
      if (lpos >= sizeof(line) - 1) {
        overflow = true;
      } else {
        line[lpos++] = c;
      }
    }
  }
}

static void bleStart() {
  char name[32];
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_BT);
  snprintf(name, sizeof(name), BUDDY_BRAND_PREFIX "%02X%02X", mac[4], mac[5]);
  Serial.printf("[ble] advertising as %s\n", name);

  BLEDevice::init(name);
  g_server = BLEDevice::createServer();
  g_server->setCallbacks(new ServerCb());

  // Standard NUS service (claimed for Claude Desktop compat; not used by daemon)
  BLEService* nus = g_server->createService(NUS_SVC_UUID);
  g_nus_tx = nus->createCharacteristic(NUS_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  g_nus_tx->addDescriptor(new BLE2902());
  BLECharacteristic* nus_rx = nus->createCharacteristic(
      NUS_RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  nus_rx->setCallbacks(new RxCb());
  nus->start();

  // Debug NUS service — daemon actually talks here.
  BLEService* dbg = g_server->createService(DBG_SVC_UUID);
  g_dbg_tx = dbg->createCharacteristic(DBG_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  g_dbg_tx->addDescriptor(new BLE2902());
  BLECharacteristic* dbg_rx = dbg->createCharacteristic(
      DBG_RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  dbg_rx->setCallbacks(new RxCb());
  dbg->start();

  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SVC_UUID);
  adv->addServiceUUID(DBG_SVC_UUID);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
}

// Optional notify so daemon's BleWriter on_tx_line callback sees us alive.
static void sendKeepalive() {
  if (!g_connected || !g_dbg_tx) return;
  const char* ka = "{\"hello\":\"stackchan\"}\n";
  g_dbg_tx->setValue((uint8_t*)ka, strlen(ka));
  g_dbg_tx->notify();
}

// ---------------------------------------------------------------------------
void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[boot] StackChan buddy firmware");

  // Settings come from NVS (Preferences). Load early so brightness +
  // volume are correct from the first paint/play. character/motion
  // configuration gets applied later, after those subsystems init.
  settingsInit();

  // Character pack pick order: NVS-stored name → build-flag default →
  // autodetect first /characters/<dir>.
  const char* char_name = settingsGetCharName();
  if (!char_name || !*char_name) {
#ifdef BUDDY_DEFAULT_CHAR
    char_name = BUDDY_DEFAULT_CHAR;
#else
    char_name = nullptr;
#endif
  }
  characterInit(char_name);
  characterSetState(CHAR_SLEEP);
  characterSetMsg("waking up...");

  // Speaker + preloaded WAV clips. Must come after M5.begin (speaker)
  // and after characterInit (which mounts LittleFS — soundInit reuses
  // that mount, so order matters).
  soundInit();

  // Body servos + I/O expander + RGB strip via StackChan-BSP. Powers
  // up the servo rail and homes both axes. Conservative move speeds in
  // motion.cpp keep peak current within USB-only budget.
  motionInit();
  motionSetEnabled(settingsGetMotionEnabled());
  motionSetIdleWiggle(settingsGetIdleWiggleEnabled());

  bleStart();
}

void loop() {
  M5.update();
  drainRx();          // moved off BTC_TASK — see RxCb comment
  characterTick();
  motionTick();

  uint32_t now = millis();

  // Held-celebrate → fall back to IDLE (or BUSY if still running) once the
  // dwell expires. Re-evaluating from g_last_rx_ms keeps idle detection
  // simple — daemon will re-emit current state on next event anyway.
  if (g_celebrate_until && now > g_celebrate_until) {
    g_celebrate_until = 0;
    if (g_cur_state == CHAR_CELEBRATE) {
      g_cur_state = CHAR_IDLE;
      characterSetState(CHAR_IDLE);
      motionSetState(CHAR_IDLE);
    }
  }

  // 20s silence → SLEEP.
  if (g_last_rx_ms != 0 && (now - g_last_rx_ms) > 20000 &&
      g_cur_state != CHAR_SLEEP) {
    g_cur_state = CHAR_SLEEP;
    characterSetState(CHAR_SLEEP);
    motionSetState(CHAR_SLEEP);
    characterSetMsg("");
    Serial.println("[state] idle -> SLEEP");
  }

  // Daemon's BleWriter expects something on NUS TX every <30s.
  static uint32_t last_ka = 0;
  if (now - last_ka > 10000) {
    last_ka = now;
    sendKeepalive();
  }
}
