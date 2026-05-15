// Native unit tests for src/stackchan/permission_ack.h.
// Run with:  pio test -e native
//
// Builds the {"cmd":"permission","id":"<id>","decision":"<once|deny>"}\n
// JSON line the firmware emits on debug-TX after a confirmed gesture.
// Matches the existing wire convention used by the Plus2 stick A/B path
// (src/main.cpp:1238) — decision strings are "once" / "deny", not
// "approve" / "deny" (which is the daemon-side semantic). Mismatches here
// silently break Claude Code permission resolution end-to-end.

#include <unity.h>
#include <string.h>
#include "permission_ack.h"

void setUp() {}
void tearDown() {}

void test_builds_once_ack_correctly() {
    char buf[128];
    size_t n = buildPermissionAck("req_abc123", "once", buf, sizeof(buf));
    const char* expect =
        "{\"cmd\":\"permission\",\"id\":\"req_abc123\",\"decision\":\"once\"}\n";
    TEST_ASSERT_EQUAL_UINT(strlen(expect), n);
    TEST_ASSERT_EQUAL_STRING(expect, buf);
}

void test_builds_deny_ack_correctly() {
    char buf[128];
    size_t n = buildPermissionAck("req_xyz", "deny", buf, sizeof(buf));
    const char* expect =
        "{\"cmd\":\"permission\",\"id\":\"req_xyz\",\"decision\":\"deny\"}\n";
    TEST_ASSERT_EQUAL_UINT(strlen(expect), n);
    TEST_ASSERT_EQUAL_STRING(expect, buf);
}

void test_rejects_unknown_decision() {
    char buf[128];
    // "approve" is the daemon-side semantic, NOT a wire decision —
    // buildPermissionAck must refuse to emit it.
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck("id", "approve", buf, sizeof(buf)));
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck("id", "", buf, sizeof(buf)));
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck("id", "yes", buf, sizeof(buf)));
}

void test_rejects_null_inputs() {
    char buf[128];
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck(nullptr, "once", buf, sizeof(buf)));
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck("id", nullptr, buf, sizeof(buf)));
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck("id", "once", nullptr, sizeof(buf)));
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck("id", "once", buf, 0));
}

void test_buffer_too_small_returns_zero() {
    char buf[16];  // too small for the full line
    TEST_ASSERT_EQUAL_UINT(0, buildPermissionAck("req_abc123", "once", buf, sizeof(buf)));
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_builds_once_ack_correctly);
    RUN_TEST(test_builds_deny_ack_correctly);
    RUN_TEST(test_rejects_unknown_decision);
    RUN_TEST(test_rejects_null_inputs);
    RUN_TEST(test_buffer_too_small_returns_zero);
    return UNITY_END();
}
