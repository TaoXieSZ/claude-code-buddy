#include "agentfarm_client.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>

void AgentFarmClient::begin(const char* ssid, const char* pass,
                            const char* host, uint16_t port,
                            const char* secret) {
  ssid_ = ssid;
  pass_ = pass;
  host_ = host;
  port_ = port;
  secret_ = secret;
  status_ = AFStatus::Connecting;
#ifdef AF_WIFI_SETPINS_TAB5
  // Tab5 (ESP32-P4) rides WiFi over the C6 via ESP-Hosted/SDIO — the pin map
  // MUST be set before the first WiFi call. (Mirrors src/tab5/main.cpp.)
  WiFi.setPins(/*clk*/12, /*cmd*/13, /*d0*/11, /*d1*/10, /*d2*/9, /*d3*/8,
               /*rst*/15);
#endif
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid_, pass_);
  lastWifiTryMs_ = millis();
}

void AgentFarmClient::ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;

  // Reconnect with a gentle cadence so we don't spin.
  if (status_ != AFStatus::Connecting) status_ = AFStatus::Offline;
  const uint32_t now = millis();
  if (now - lastWifiTryMs_ < 3000) return;
  lastWifiTryMs_ = now;
  WiFi.disconnect();
  WiFi.begin(ssid_, pass_);
}

void AgentFarmClient::loop() {
  ensureWifi();
  if (WiFi.status() != WL_CONNECTED) return;

  const uint32_t now = millis();
  if (now - lastPollMs_ < pollIntervalMs_ + backoffMs_) return;
  lastPollMs_ = now;
  poll();
}

void AgentFarmClient::poll() {
  HTTPClient http;
  String url = "http://";
  url += host_;
  url += ":";
  url += String(port_);
  url += "/api/logs?limit=";
  url += String(kPollLimit);

  if (!http.begin(url)) {
    status_ = AFStatus::Offline;
    backoffMs_ = min<uint32_t>(backoffMs_ + 2000, 20000);
    return;
  }
  http.addHeader("Authorization", String("Bearer ") + secret_);
  http.setTimeout(4000);

  const int code = http.GET();
  if (code == 401 || code == 403) {
    status_ = AFStatus::AuthError;
    backoffMs_ = min<uint32_t>(backoffMs_ + 4000, 30000);
    http.end();
    return;
  }
  if (code != 200) {
    status_ = AFStatus::Offline;
    backoffMs_ = min<uint32_t>(backoffMs_ + 2000, 20000);
    http.end();
    return;
  }

  // Stream-parse with a field filter to keep memory bounded regardless of
  // how large prompt_summary/response are.
  JsonDocument filter;
  JsonObject elem = filter["items"].add<JsonObject>();
  elem["timestamp"] = true;
  elem["trigger_name"] = true;
  elem["trigger_type"] = true;
  elem["agent_name"] = true;
  elem["result"] = true;
  elem["error"] = true;

  JsonDocument doc;
  DeserializationError err = deserializeJson(
      doc, http.getStream(), DeserializationOption::Filter(filter));
  http.end();
  if (err) {
    status_ = AFStatus::Offline;
    backoffMs_ = min<uint32_t>(backoffMs_ + 2000, 20000);
    return;
  }

  status_ = AFStatus::Online;
  backoffMs_ = 0;

  JsonArray items = doc["items"].as<JsonArray>();
  if (items.isNull() || items.size() == 0) return;  // items[0] is newest

  // Build the full snapshot (newest-first) into history_.
  history_.clear();
  for (JsonObject it : items) {
    TriggerLog log;
    log.timestamp = String((const char*)(it["timestamp"] | ""));
    log.triggerName = String((const char*)(it["trigger_name"] | ""));
    log.triggerTypeRaw = String((const char*)(it["trigger_type"] | ""));
    log.agentName = String((const char*)(it["agent_name"] | ""));
    log.resultRaw = String((const char*)(it["result"] | ""));
    log.error = String((const char*)(it["error"] | ""));
    history_.push_back(log);
    if (history_.size() >= kHistoryMax) break;
  }

  const String newest = history_.front().timestamp;

  if (!primed_) {
    // First successful poll: adopt the cursor without alerting on the backlog.
    primed_ = true;
    lastSeenTs_ = newest;
    return;
  }

  // Host log reset (in-memory log cleared on restart): newest is older-or-equal
  // than our cursor. Re-sync silently.
  if (newest <= lastSeenTs_) {
    lastSeenTs_ = newest;
    return;
  }

  // Collect entries strictly newer than the cursor, emit oldest -> newest.
  for (auto rit = history_.rbegin(); rit != history_.rend(); ++rit) {
    if (rit->timestamp > lastSeenTs_) fresh_.push_back(*rit);
  }
  lastSeenTs_ = newest;
}
