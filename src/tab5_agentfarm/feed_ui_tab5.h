// FeedUITab5 — Agent Farm trigger feed + pet mood for the Tab5 1280x720 LCD.
// Reuses the shared AgentFarmClient transport; only the rendering differs from
// the Cardputer UI (much bigger screen, more feed rows, larger pet).
#pragma once

#include <Arduino.h>
#include <vector>
#include "../agentfarm_feed/serial_feed_client.h"
#include "../agentfarm_feed/trigger_log.h"

class FeedUITab5 {
 public:
  void begin();
  void onNewEntries(const std::vector<TriggerLog>& fresh);
  void tick(const SerialFeedClient& client);

 private:
  enum class Mood { Idle, Happy, Worried, Sleep };

  void render(const SerialFeedClient& client);
  void drawHeader(const SerialFeedClient& client);
  void drawPet(int x, int y, int w, int h);
  void drawFeed(const SerialFeedClient& client, int x, int y, int w, int h);
  void wake();

  Mood mood_ = Mood::Idle;
  AFStatus lastStatus_ = AFStatus::Connecting;
  bool dirty_ = true;
  bool dimmed_ = false;
  uint32_t lastEventMs_ = 0;
  uint32_t happyUntilMs_ = 0;

  static const uint32_t kSleepAfterMs = 120000;  // doze after 2 min quiet
  static const uint8_t kBrightOn = 255;
  static const uint8_t kBrightDim = 64;
};
