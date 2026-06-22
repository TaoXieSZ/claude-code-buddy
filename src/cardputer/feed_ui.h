// FeedUI — renders the trigger feed + an aggregate "pet mood" on the Cardputer
// 240x135 LCD, plays short chirps on notable events, and dozes when idle.
#pragma once

#include <Arduino.h>
#include <vector>
#include "../agentfarm_feed/agentfarm_client.h"
#include "../agentfarm_feed/trigger_log.h"

class FeedUI {
 public:
  void begin();

  // New entries arrived (chronological). Updates mood, plays a chirp, wakes.
  void onNewEntries(const std::vector<TriggerLog>& fresh);

  // Keyboard scroll through buffered history. delta -1 = newer, +1 = older.
  void scroll(int delta);

  // Call every loop: handles the idle->sleep timeout and redraws if needed.
  void tick(const AgentFarmClient& client);

 private:
  enum class Mood { Idle, Happy, Worried, Sleep };

  void render(const AgentFarmClient& client);
  void drawPet(int x, int y, int w, int h);
  void drawStatusBar(const AgentFarmClient& client);
  void drawFeed(const AgentFarmClient& client, int x, int y, int w, int h);
  void wake();

  Mood mood_ = Mood::Idle;
  AFStatus lastStatus_ = AFStatus::Connecting;
  int scrollOff_ = 0;
  bool dirty_ = true;
  bool dimmed_ = false;
  uint32_t lastEventMs_ = 0;
  uint32_t happyUntilMs_ = 0;

  static const uint32_t kSleepAfterMs = 60000;  // doze after 60s quiet
  static const uint8_t kBrightOn = 180;
  static const uint8_t kBrightDim = 18;
};
