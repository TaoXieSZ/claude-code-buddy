#include "serial_feed_client.h"

#include <ArduinoJson.h>

void SerialFeedClient::begin() {
  len_ = 0;
  gotData_ = false;
  lastRxMs_ = 0;
}

AFStatus SerialFeedClient::status() const {
  if (!gotData_) return AFStatus::Connecting;
  return (millis() - lastRxMs_ < kStaleMs) ? AFStatus::Online
                                           : AFStatus::Offline;
}

void SerialFeedClient::loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      buf_[len_] = 0;
      if (len_ > 0) handleLine(buf_);
      len_ = 0;
      continue;
    }
    if (len_ < kBufMax - 1) {
      buf_[len_++] = c;
    } else {
      len_ = 0;  // overflow — drop the malformed line
    }
  }
}

void SerialFeedClient::handleLine(const char* line) {
  if (line[0] != '{') return;  // ignore non-JSON noise

  JsonDocument doc;
  if (deserializeJson(doc, line)) return;

  // heartbeat keeps the link "online" between firings
  if (!doc["hb"].isNull()) {
    gotData_ = true;
    lastRxMs_ = millis();
    return;
  }

  const char* name = doc["n"];
  if (!name) return;

  TriggerLog log;
  log.timestamp = String((const char*)(doc["t"] | ""));
  log.triggerName = String(name);
  log.triggerTypeRaw = String((const char*)(doc["ty"] | ""));
  log.agentName = String((const char*)(doc["a"] | ""));
  log.resultRaw = String((const char*)(doc["r"] | ""));
  log.error = String((const char*)(doc["e"] | ""));

  // newest-first history
  history_.insert(history_.begin(), log);
  if (history_.size() > kHistoryMax) history_.pop_back();

  gotData_ = true;
  lastRxMs_ = millis();

  // `new:false` snapshot lines prime history silently; live ones react.
  bool isNew = doc["new"] | true;
  if (isNew) fresh_.push_back(log);
}
