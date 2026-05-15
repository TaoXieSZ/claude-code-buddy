// Permission-ack JSON builder for the StackChan gesture-approve path.
// P1 of openspec/changes/2026-05-15-0003-stackchan-camera-gestures.
//
// Produces "{\"cmd\":\"permission\",\"id\":\"<id>\",\"decision\":\"<dec>\"}\n"
// where dec is the wire decision string ("once" = approve, "deny" = deny),
// matching the existing Plus2 stick A/B convention (src/main.cpp:1238).
// The daemon's on_stick_line dispatcher resolves the pending future by
// rid and replies that decision verbatim to Claude Code.
//
// Header-only + inline so the native test env can compile it without
// ArduinoJson. id is copied verbatim — caller MUST ensure it contains no
// JSON-breaking chars (Claude Code rids are alphanumeric+underscore).

#pragma once

#include <stddef.h>
#include <stdio.h>
#include <string.h>

inline size_t buildPermissionAck(const char* id, const char* decision,
                                 char* out, size_t cap) {
    if (!id || !decision || !out || cap == 0) return 0;
    // Whitelist the two wire decision strings — "approve" is a daemon-side
    // semantic and would be rejected by the existing daemon dispatcher.
    if (strcmp(decision, "once") != 0 && strcmp(decision, "deny") != 0) {
        return 0;
    }
    int n = snprintf(out, cap,
                     "{\"cmd\":\"permission\",\"id\":\"%s\",\"decision\":\"%s\"}\n",
                     id, decision);
    if (n < 0 || (size_t)n >= cap) return 0;  // overflow → drop the line
    return (size_t)n;
}
