// SerialFeedClient — read-only USB-serial transport for the Agent Farm feed.
// A Mac-side bridge (tools/agentfarm-usb-bridge/bridge.py) polls Agent Farm's
// admin API on localhost and pushes newline-delimited JSON to the device's USB
// CDC. This client parses those lines into TriggerLogs, exposing the same
// surface as AgentFarmClient so the UI is unchanged.
//
// Wire format (one JSON object per line):
//   {"t":"<iso>","n":"<trigger_name>","ty":"<type>","a":"<agent>","r":"<result>","new":true}
//   {"hb":1}                                            // heartbeat (link alive)
// `new:false` lines are the initial snapshot (history only, no pet reaction).
#pragma once

#include <Arduino.h>
#include <vector>
#include "trigger_log.h"  // provides AFStatus + TriggerLog

class SerialFeedClient {
 public:
  void begin();  // Serial is configured by main(); this just resets state
  void loop();   // drain available serial lines

  AFStatus status() const;
  std::vector<TriggerLog>& freshEntries() { return fresh_; }
  const std::vector<TriggerLog>& history() const { return history_; }

 private:
  void handleLine(const char* line);

  std::vector<TriggerLog> history_;  // newest-first, bounded
  std::vector<TriggerLog> fresh_;    // consumed by the UI each loop
  uint32_t lastRxMs_ = 0;
  bool gotData_ = false;

  static const size_t kHistoryMax = 40;
  static const size_t kBufMax = 600;  // one JSON line
  static const uint32_t kStaleMs = 15000;
  char buf_[kBufMax];
  size_t len_ = 0;
};
