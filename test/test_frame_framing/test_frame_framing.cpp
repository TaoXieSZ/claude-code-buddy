// Native (off-device) unit tests for src/stackchan/frame_framing.h.
// Run with:  pio test -e native
//
// The camera-stream wire format (P0 of the camera-gesture pipeline, see
// openspec/changes/2026-05-15-0003-stackchan-camera-gestures/) prefixes each
// JPEG payload with a 4-byte little-endian length. The framing builder is
// pure logic — no Arduino, no esp_camera — so it lives in a header and is
// host-testable.

#include <unity.h>
#include <stdint.h>
#include "frame_framing.h"

void setUp() {}
void tearDown() {}

// ─── writeFrameLengthLE ───────────────────────────────────────────────

void test_writeFrameLengthLE_zero() {
    uint8_t buf[4] = {0xAA, 0xAA, 0xAA, 0xAA};
    writeFrameLengthLE(0, buf);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[0]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[1]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[2]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[3]);
}

void test_writeFrameLengthLE_one() {
    uint8_t buf[4] = {0};
    writeFrameLengthLE(1, buf);
    TEST_ASSERT_EQUAL_UINT8(0x01, buf[0]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[1]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[2]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[3]);
}

void test_writeFrameLengthLE_256_crosses_byte_boundary() {
    uint8_t buf[4] = {0};
    writeFrameLengthLE(256, buf);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[0]);
    TEST_ASSERT_EQUAL_UINT8(0x01, buf[1]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[2]);
    TEST_ASSERT_EQUAL_UINT8(0x00, buf[3]);
}

void test_writeFrameLengthLE_distinct_bytes() {
    // 0x12345678 must come out as [0x78, 0x56, 0x34, 0x12] (LE).
    // Catches accidental big-endian / network-order regressions.
    uint8_t buf[4] = {0};
    writeFrameLengthLE(0x12345678, buf);
    TEST_ASSERT_EQUAL_UINT8(0x78, buf[0]);
    TEST_ASSERT_EQUAL_UINT8(0x56, buf[1]);
    TEST_ASSERT_EQUAL_UINT8(0x34, buf[2]);
    TEST_ASSERT_EQUAL_UINT8(0x12, buf[3]);
}

void test_writeFrameLengthLE_uint32_max() {
    uint8_t buf[4] = {0};
    writeFrameLengthLE(0xFFFFFFFFu, buf);
    TEST_ASSERT_EQUAL_UINT8(0xFF, buf[0]);
    TEST_ASSERT_EQUAL_UINT8(0xFF, buf[1]);
    TEST_ASSERT_EQUAL_UINT8(0xFF, buf[2]);
    TEST_ASSERT_EQUAL_UINT8(0xFF, buf[3]);
}

// ─── entry point ──────────────────────────────────────────────────────

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_writeFrameLengthLE_zero);
    RUN_TEST(test_writeFrameLengthLE_one);
    RUN_TEST(test_writeFrameLengthLE_256_crosses_byte_boundary);
    RUN_TEST(test_writeFrameLengthLE_distinct_bytes);
    RUN_TEST(test_writeFrameLengthLE_uint32_max);
    return UNITY_END();
}
