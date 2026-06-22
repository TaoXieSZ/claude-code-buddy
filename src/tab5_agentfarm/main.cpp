// Tab5 (ESP32-P4) — Agent Farm desk pet (USB-serial transport)
//
// Reads the Agent Farm trigger feed over USB-CDC from a Mac-side bridge
// (tools/agentfarm-usb-bridge/bridge.py) instead of WiFi — the bridge reaches
// Agent Farm on localhost, so this works anywhere regardless of the WiFi the
// laptop is on. Same dashboard UI + clawd avatar; only the data source changed.
#include <Arduino.h>
#include <M5Unified.h>

#include "../agentfarm_feed/serial_feed_client.h"
#include "feed_ui_tab5.h"

static SerialFeedClient gClient;
static FeedUITab5 gUi;

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  // Trigger lines (~150B) can arrive back-to-back on the snapshot; size the
  // USB-CDC RX ring before begin() so a burst doesn't overflow the default 256.
  Serial.setRxBufferSize(4096);
  Serial.begin(115200);
  for (int tries = 0; M5.Display.width() == 0 && tries < 3; tries++) {
    delay(250);
    M5.Display.init();
  }
  gUi.begin();
  gClient.begin();
}

void loop() {
  M5.update();
  gClient.loop();

  std::vector<TriggerLog>& fresh = gClient.freshEntries();
  if (!fresh.empty()) {
    gUi.onNewEntries(fresh);
    fresh.clear();
  }

  gUi.tick(gClient);
  delay(10);
}
