// Cardputer ADV — Agent Farm desk pet (v1)
//
// Read-only WiFi client of the Agent Farm trigger-cursor admin API. Polls
// GET /api/logs, shows every trigger firing as a feed, and reacts as a pet.
// No BLE, no Mac daemon, no write-back to Agent Farm.
#include <Arduino.h>
#include <M5Cardputer.h>

#include "../agentfarm_feed/agentfarm_client.h"
#include "feed_ui.h"

// Credentials come from wifi_secrets.ini via -D build flags (see platformio.ini).
// Defaults keep the build green even with a clean (placeholder) secrets file.
#ifndef AF_WIFI_SSID
#define AF_WIFI_SSID ""
#endif
#ifndef AF_WIFI_PASS
#define AF_WIFI_PASS ""
#endif
#ifndef AF_HOST
#define AF_HOST ""
#endif
#ifndef AF_PORT
#define AF_PORT 60360
#endif
#ifndef AF_SECRET
#define AF_SECRET ""
#endif

static AgentFarmClient gClient;
static FeedUI gUi;

void setup() {
  auto cfg = M5.config();
  M5Cardputer.begin(cfg, true);  // true = enable keyboard
  gUi.begin();
  gClient.begin(AF_WIFI_SSID, AF_WIFI_PASS, AF_HOST,
                (uint16_t)AF_PORT, AF_SECRET);
}

void loop() {
  M5Cardputer.update();
  gClient.loop();

  std::vector<TriggerLog>& fresh = gClient.freshEntries();
  if (!fresh.empty()) {
    gUi.onNewEntries(fresh);
    fresh.clear();
  }

  // Keyboard scroll: ';' and '.' carry the up/down arrow glyphs on Cardputer.
  if (M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed()) {
    Keyboard_Class::KeysState st = M5Cardputer.Keyboard.keysState();
    for (char c : st.word) {
      if (c == ';') gUi.scroll(-1);
      else if (c == '.') gUi.scroll(1);
    }
  }

  gUi.tick(gClient);
  delay(5);
}
