// TriggerLog — mirror of the Agent Farm trigger-cursor TriggerLog
// (trigger-cursor/src/dispatcher.ts:17). Only the fields the desk pet needs.
// Shared by all device targets (cardputer, tab5, ...).
#pragma once

#include <Arduino.h>

// Link/transport status, shared by the WiFi and USB-serial feed clients.
enum class AFStatus {
  Connecting,  // joining WiFi / waiting for first serial data
  Online,      // last poll/frame succeeded recently
  Offline,     // host/link unreachable
  AuthError,   // 401/403 — bad/missing secret (WiFi only)
};

enum class TrigResult {
  Success,
  Error,
  Queued,
  SkippedBusy,
  SkippedPaused,
  SkippedNoMatch,
  Unknown,
};

enum class TrigType {
  Slack,
  Cron,
  Manual,
  Jira,
  Unknown,
};

struct TriggerLog {
  String timestamp;     // ISO-8601 UTC, lexicographically sortable
  String triggerName;
  String triggerTypeRaw;
  String agentName;
  String resultRaw;
  String error;
};

inline TrigResult parseResult(const String& r) {
  if (r == "success") return TrigResult::Success;
  if (r == "error") return TrigResult::Error;
  if (r == "queued") return TrigResult::Queued;
  if (r == "skipped_busy") return TrigResult::SkippedBusy;
  if (r == "skipped_paused") return TrigResult::SkippedPaused;
  if (r == "skipped_no_match") return TrigResult::SkippedNoMatch;
  return TrigResult::Unknown;
}

inline TrigType parseType(const String& t) {
  if (t == "slack_event") return TrigType::Slack;
  if (t == "cron") return TrigType::Cron;
  if (t == "manual") return TrigType::Manual;
  if (t == "jira_poll") return TrigType::Jira;
  return TrigType::Unknown;
}

// Short ASCII glyph for each trigger source (no icon font in v1).
inline const char* typeGlyph(TrigType t) {
  switch (t) {
    case TrigType::Slack: return "#";   // channel
    case TrigType::Cron: return "@";    // schedule
    case TrigType::Manual: return ">";  // hand-fired
    case TrigType::Jira: return "J";
    default: return "?";
  }
}

inline const char* resultTag(TrigResult r) {
  switch (r) {
    case TrigResult::Success: return "ok";
    case TrigResult::Error: return "ERR";
    case TrigResult::Queued: return "que";
    case TrigResult::SkippedBusy: return "busy";
    case TrigResult::SkippedPaused: return "paus";
    case TrigResult::SkippedNoMatch: return "skip";
    default: return "?";
  }
}

// HH:MM:SS slice of an ISO-8601 timestamp (UTC). Empty string if malformed.
// Devices have no RTC sync in v1, so we show the firing time from the log.
inline String hhmmss(const String& iso) {
  // "2026-06-20T07:33:01.123Z" -> chars 11..18
  if (iso.length() < 19 || iso.charAt(10) != 'T') return String();
  return iso.substring(11, 19);
}
