// Native (off-device) unit tests for src/stackchan/color_util.h.
// Run with:  pio test -e native

#include <unity.h>
#include "color_util.h"

void setUp() {}
void tearDown() {}

// ─── parseHexColor ─────────────────────────────────────────────────────

void test_parseHexColor_pure_channels() {
    // #FF0000 → red: top 5 bits set.   #00FF00 → green: middle 6 bits.
    TEST_ASSERT_EQUAL_HEX16(0xF800, parseHexColor("#FF0000", 0x1234));
    TEST_ASSERT_EQUAL_HEX16(0x07E0, parseHexColor("#00FF00", 0x1234));
    TEST_ASSERT_EQUAL_HEX16(0x001F, parseHexColor("#0000FF", 0x1234));
}

void test_parseHexColor_white_and_black() {
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, parseHexColor("#FFFFFF", 0x1234));
    TEST_ASSERT_EQUAL_HEX16(0x0000, parseHexColor("#000000", 0x1234));
}

void test_parseHexColor_hash_optional() {
    TEST_ASSERT_EQUAL_HEX16(parseHexColor("#FF9500", 0),
                            parseHexColor("FF9500", 0));
}

void test_parseHexColor_null_and_empty_return_fallback() {
    TEST_ASSERT_EQUAL_HEX16(0xBEEF, parseHexColor(nullptr, 0xBEEF));
    TEST_ASSERT_EQUAL_HEX16(0xBEEF, parseHexColor("", 0xBEEF));
}

// ─── blend565 ──────────────────────────────────────────────────────────
// blend565 takes big-endian RGB565 (the GIF lib's palette format) and
// returns big-endian. Build BE inputs by byte-swapping logical values.

static uint16_t be(uint16_t logical) { return __builtin_bswap16(logical); }
static uint16_t logical(uint16_t be_val) { return __builtin_bswap16(be_val); }

void test_blend565_endpoints() {
    uint16_t red_be   = be(0xF800);
    uint16_t blue_be  = be(0x001F);
    // frac = 0 → full a;  frac = 256 → full b.
    TEST_ASSERT_EQUAL_HEX16(0xF800, logical(blend565(red_be, blue_be, 0)));
    TEST_ASSERT_EQUAL_HEX16(0x001F, logical(blend565(red_be, blue_be, 256)));
}

void test_blend565_midpoint_is_between() {
    uint16_t black_be = be(0x0000);
    uint16_t white_be = be(0xFFFF);
    uint16_t mid = logical(blend565(black_be, white_be, 128));
    // Halfway black→white: every channel should land mid-range, not at
    // either extreme.
    uint8_t r = (mid >> 11) & 0x1F;
    uint8_t g = (mid >> 5) & 0x3F;
    uint8_t b = mid & 0x1F;
    TEST_ASSERT_TRUE(r > 0 && r < 0x1F);
    TEST_ASSERT_TRUE(g > 0 && g < 0x3F);
    TEST_ASSERT_TRUE(b > 0 && b < 0x1F);
}

void test_blend565_same_colour_is_identity() {
    uint16_t c_be = be(0x8410);
    TEST_ASSERT_EQUAL_HEX16(0x8410, logical(blend565(c_be, c_be, 128)));
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_parseHexColor_pure_channels);
    RUN_TEST(test_parseHexColor_white_and_black);
    RUN_TEST(test_parseHexColor_hash_optional);
    RUN_TEST(test_parseHexColor_null_and_empty_return_fallback);
    RUN_TEST(test_blend565_endpoints);
    RUN_TEST(test_blend565_midpoint_is_between);
    RUN_TEST(test_blend565_same_colour_is_identity);
    return UNITY_END();
}
