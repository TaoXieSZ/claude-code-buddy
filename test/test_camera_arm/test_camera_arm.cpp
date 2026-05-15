// Native unit tests for src/stackchan/camera_arm.h.
// Run with:  pio test -e native
//
// The camera/wifi-stream lifecycle is bound to (ATTENTION state) AND (a
// prompt id we can ack). These pure helpers compute "should the camera
// be armed?" and the Arm/Disarm transition vs the prior tick — host-
// testable so the on-device wiring in main.cpp can stay minimal.

#include <unity.h>
#include "camera_arm.h"

void setUp() {}
void tearDown() {}

// ─── shouldCameraBeArmed ──────────────────────────────────────────────

void test_armed_requires_all_three_conditions() {
    // All three true → armed.
    TEST_ASSERT_TRUE (shouldCameraBeArmed(true,  true,  true));
    // Any one false → not armed.
    TEST_ASSERT_FALSE(shouldCameraBeArmed(true,  true,  false)); // no wifi creds
    TEST_ASSERT_FALSE(shouldCameraBeArmed(true,  false, true));  // no prompt id
    TEST_ASSERT_FALSE(shouldCameraBeArmed(false, true,  true));  // not ATTENTION
    // Two or three false → not armed.
    TEST_ASSERT_FALSE(shouldCameraBeArmed(false, false, false));
}

void test_armed_false_when_wifi_creds_missing() {
    // Tracks the placeholder-creds short-circuit: even with a perfect
    // prompt window, no creds → don't bounce cameraStart→Stop and mute
    // the speaker for nothing.
    TEST_ASSERT_FALSE(shouldCameraBeArmed(true, true, false));
}

// ─── cameraTransition ─────────────────────────────────────────────────

void test_transition_arm_on_false_to_true() {
    TEST_ASSERT_EQUAL(static_cast<int>(ArmTransition::Arm),
                      static_cast<int>(cameraTransition(false, true)));
}

void test_transition_disarm_on_true_to_false() {
    TEST_ASSERT_EQUAL(static_cast<int>(ArmTransition::Disarm),
                      static_cast<int>(cameraTransition(true, false)));
}

void test_transition_none_when_steady() {
    TEST_ASSERT_EQUAL(static_cast<int>(ArmTransition::None),
                      static_cast<int>(cameraTransition(false, false)));
    TEST_ASSERT_EQUAL(static_cast<int>(ArmTransition::None),
                      static_cast<int>(cameraTransition(true, true)));
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_armed_requires_all_three_conditions);
    RUN_TEST(test_armed_false_when_wifi_creds_missing);
    RUN_TEST(test_transition_arm_on_false_to_true);
    RUN_TEST(test_transition_disarm_on_true_to_false);
    RUN_TEST(test_transition_none_when_steady);
    return UNITY_END();
}
