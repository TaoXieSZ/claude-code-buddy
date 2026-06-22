// AgentFarmClient — WiFi station + read-only HTTP poller for the Agent Farm
// trigger-cursor admin API. Polls GET /api/logs, keeps a dedup cursor over the
// ISO timestamp, and surfaces newly-arrived TriggerLog entries.
//
// Hardware-agnostic: shared by the cardputer and tab5 targets. Read-only by
// design — it never writes back to Agent Farm (v1 device contract).
#pragma once

#include <Arduino.h>
#include <vector>
#include "trigger_log.h"  // provides AFStatus

class AgentFarmClient {
 public:
  void begin(const char* ssid, const char* pass, const char* host,
             uint16_t port, const char* secret);

  // Non-blocking-ish: maintains WiFi, polls on the configured interval.
  // Call every loop().
  void loop();

  AFStatus status() const { return status_; }

  // Entries that arrived since the last loop(), in chronological order
  // (oldest -> newest). Caller consumes then clears.
  std::vector<TriggerLog>& freshEntries() { return fresh_; }

  // Buffered recent history, newest-first (for keyboard/touch scrolling).
  const std::vector<TriggerLog>& history() const { return history_; }

 private:
  void ensureWifi();
  void poll();

  const char* ssid_ = "";
  const char* pass_ = "";
  const char* host_ = "";
  uint16_t port_ = 0;
  const char* secret_ = "";

  AFStatus status_ = AFStatus::Connecting;
  String lastSeenTs_;      // newest timestamp we have already shown
  bool primed_ = false;    // first successful poll just sets the cursor

  std::vector<TriggerLog> history_;  // newest-first, bounded
  std::vector<TriggerLog> fresh_;    // produced each poll, consumed by UI

  uint32_t lastPollMs_ = 0;
  uint32_t pollIntervalMs_ = 4000;
  uint32_t backoffMs_ = 0;           // added to interval after failures
  uint32_t lastWifiTryMs_ = 0;

  static const size_t kHistoryMax = 40;
  static const int kPollLimit = 12;  // /api/logs?limit=
};
