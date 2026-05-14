// color_util.h — pure RGB565 colour helpers for the StackChan firmware.
//
// Extracted from character_chan.cpp so they can be unit-tested off-device
// (pio test -e native). Zero hardware dependency — no M5Unified, no
// AnimatedGIF, no LittleFS. Just integer maths over uint16_t colours.
#pragma once

#include <cstdint>
#include <cstdlib>

// Parse a CSS-style hex colour ("#RRGGBB" or "RRGGBB") into RGB565.
// Returns the fallback `fb` for null/empty input. Malformed input is
// tolerated the way strtoul tolerates it (stops at the first bad char).
inline uint16_t parseHexColor(const char* s, uint16_t fb) {
  if (!s || !*s) return fb;
  if (*s == '#') s++;
  uint32_t v = strtoul(s, nullptr, 16);
  return (uint16_t)(((v >> 19) & 0x1F) << 11 |
                    ((v >> 10) & 0x3F) << 5  |
                    ((v >> 3)  & 0x1F));
}

// Blend two RGB565 colours. `frac` is 0..256 fixed-point (0 = full a,
// 256 = full b).
//
// IMPORTANT: the GIF library is initialised with GIF_PALETTE_RGB565_BE,
// so palette entries are big-endian — on a little-endian CPU the raw
// uint16_t has its bytes swapped vs. logical RGB565. The nearest-neighbor
// path copied palette values through verbatim so byte order never
// mattered; here we interpret the bits, so we bswap to logical layout,
// lerp each channel, and bswap back to BE for pushImage.
inline uint16_t blend565(uint16_t a_be, uint16_t b_be, int frac) {
  uint16_t a = __builtin_bswap16(a_be);
  uint16_t b = __builtin_bswap16(b_be);
  int inv = 256 - frac;
  int r = (((a >> 11) & 0x1F) * inv + ((b >> 11) & 0x1F) * frac) >> 8;
  int g = (((a >> 5)  & 0x3F) * inv + ((b >> 5)  & 0x3F) * frac) >> 8;
  int bl= (( a        & 0x1F) * inv + ( b        & 0x1F) * frac) >> 8;
  return __builtin_bswap16((uint16_t)((r << 11) | (g << 5) | bl));
}
