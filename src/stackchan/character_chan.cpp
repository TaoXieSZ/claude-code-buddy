// character_chan.cpp — CoreS3 GIF playback for the StackChan buddy.
//
// Uses bitbank2/AnimatedGIF + LittleFS. Scanlines from the GIF library
// are nearest-neighbor float-scaled to a UNIFORM output height
// (TARGET_H) so that switching between sleep/busy/attention/etc. keeps
// the character a consistent size on screen — only the aspect ratio
// (width) varies per state. Status bar is fixed-height at the bottom
// with two text rows: msg (size 2) and stats (size 1).
//
// Orientation: portrait was tried first (rotation 0, 240×320) so tall
// GIFs would fit at integer 2× — but assembled in the StackChan body
// CoreS3 sits landscape, so we use rotation 1 (320×240) and float-scale
// instead.

#include "character_chan.h"
#include "color_util.h"   // parseHexColor, blend565 — unit-tested off-device
#include <M5Unified.h>
#include <LittleFS.h>
#include <AnimatedGIF.h>
#include <ArduinoJson.h>

namespace {

// --- Geometry --------------------------------------------------------------
// 2026-05-14 layout v3 — 2-row OMC-HUD card on top (openspec 0002):
//   ┌── HUD card (2 rows, 50px) ──────────────┐
//   │ <model>                      <ctx>% ctx │
//   │ <session> <tokens> tok    5h N%  7d N%  │
//   ├────────────────────────┬────────────────┤
//   │                        │  speech bubble │
//   │     GIF face           │◀ state header  │
//   │     176 × 178          │  msg wrap      │
//   │                        ├────────────────┤
//   │                        │  tool chip     │
//   └────────────────────────┴────────────────┘
// The face is the personality and keeps the dominant area; the right
// column is a state-coloured bubble + tool chip; the top card carries
// the live OMC-HUD metrics relayed by the cc-bridge `hud` event.
// Animal-Crossing / NookPhone palette (ref: guokaigdg/animal-island-ui
// design tokens). Warm cream "cards" with a 2px brown border and the
// signature flat offset drop-shadow, floating on a dark canvas like
// NookPhone app tiles. Screen bg stays dark (keeps the GIF looking
// good — user already approved it); manifest bg still drives GIF
// transparency inside CHAR_BOX.
constexpr uint16_t SCREEN_BG    = 0x0000;   // #000000 dark canvas
constexpr uint16_t CARD_FILL    = 0xF79B;   // #F7F3DF warm cream
constexpr uint16_t CARD_BORDER  = 0xAD33;   // #AAA69D warm grey border
constexpr uint16_t CARD_SHADOW  = 0xBD74;   // #BDAEA0 flat offset shadow
constexpr uint16_t CARD_DIV     = 0xE71C;   // #E8E2D6 light divider
constexpr uint16_t CARD_TEXT    = 0x7A64;   // #794F27 warm brown text
constexpr uint16_t CARD_TEXT_SEC= 0x9C8F;   // #9F927D secondary brown
constexpr int      CARD_BW      = 2;        // border width
constexpr int      CARD_SHADOW_DY = 4;      // shadow vertical offset

constexpr int  HUD_Y         = 2;
constexpr int  HUD_H         = 50;   // 2-row card; +CARD_SHADOW_DY ends at 56
constexpr int  CHAR_BOX_X    = 4;
constexpr int  CHAR_BOX_Y    = 58;
constexpr int  CHAR_BOX_W    = 176;
constexpr int  CHAR_BOX_H    = 178;
constexpr int  BUBBLE_X      = 184;
constexpr int  BUBBLE_Y      = 60;
constexpr int  BUBBLE_W      = 132;
constexpr int  BUBBLE_H      = 132;
constexpr int  BUBBLE_R      = 14;
constexpr int  BUBBLE_PAD    = 8;
constexpr int  BUBBLE_HEAD_H = 20;   // top status strip inside card
constexpr int  TOOL_CHIP_X   = 184;
constexpr int  TOOL_CHIP_Y   = 200;
constexpr int  TOOL_CHIP_W   = 132;
constexpr int  TOOL_CHIP_H   = 32;
constexpr int  TOOL_CHIP_R   = 16;   // full-pill radius (= h/2)

// State → accent colour for the bubble header strip and the chip dot.
// Animal-Crossing palette: teal IDLE, warning-yellow BUSY, error-red
// ATTN, success-green DONE, warm-brown SLEEP.
uint16_t accentForState(uint8_t s) {
  switch (s) {
    case 3 /*CHAR_ATTENTION*/: return 0xE2CB;   // #E05A5A error red
    case 2 /*CHAR_BUSY*/:      return 0xF603;   // #F5C31C warning yellow
    case 1 /*CHAR_IDLE*/:      return 0x1E57;   // #19C8B9 primary teal
    case 4 /*CHAR_CELEBRATE*/: return 0x6DC5;   // #6FBA2C success green
    case 0 /*CHAR_SLEEP*/:     return 0x9C0B;   // #9A835A warm brown
    default:                   return 0x9C0B;
  }
}
// Header-strip text colour: ACNH cards use brown on light variants
// (yellow), white on saturated ones.
uint16_t headerTextForState(uint8_t s) {
  return (s == 2 /*BUSY/yellow*/) ? CARD_TEXT : 0xFFFF;
}
const char* labelForState(uint8_t s) {
  switch (s) {
    case 0: return "SLEEP";
    case 1: return "IDLE";
    case 2: return "BUSY";
    case 3: return "ATTN";
    case 4: return "DONE";
    case 5: return "ERR";
    case 6: return "<3";
    default: return "";
  }
}

// --- File mapping ----------------------------------------------------------
const char* STATE_FILES[CHAR_N_STATES] = {
  "sleep.gif", "idle.gif", "busy_0.gif", "attention.gif",
  "celebrate.gif", "dizzy.gif", "heart.gif",
};

// --- Runtime state ---------------------------------------------------------
AnimatedGIF  g_gif;
File         g_file;
char         g_base[48]      = "";
char         g_full_path[80] = "";
uint16_t     g_bg            = 0x0000;
uint8_t      g_cur_state     = 0xFF;
bool         g_gif_open      = false;

int          g_src_w   = 0;     // current GIF native size
int          g_src_h   = 0;
int          g_out_w   = 0;     // current scaled output size
int          g_out_h   = 0;
int          g_gx      = 0;     // top-left of output region on LCD
int          g_gy      = 0;
float        g_scale_f = 1.0f;

uint32_t     g_next_frame_at = 0;

// Status bar — msg + stats. Each repainted lazily on dirty check.
char         g_msg[64]       = "";
char         g_msg_drawn[64] = "";
int          g_running       = 0;
int          g_waiting       = 0;
uint32_t     g_tokens        = 0;
char         g_tool[24]      = "";
char         g_stats_drawn[128] = "";   // last rendered stats line

// HUD card metrics — from the cc-bridge `hud` event (openspec 0002).
int          g_context_pct   = 0;
char         g_model[24]     = "";
uint32_t     g_hud_tokens    = 0;
int          g_limit_5h      = 0;
int          g_limit_7d      = 0;
uint32_t     g_session_ms    = 0;

// Scanline buffer — sized for max output width at TARGET_H scale. The
// largest output width is sleep.gif/busy.gif at aspect ratio ~120:118
// scaled to height 170 → ~172 wide. 360 is comfortable headroom.
uint16_t     g_line[360];

// parseHexColor + blend565 live in color_util.h (pure, unit-tested).

// --- AnimatedGIF file callbacks --------------------------------------------
void* gifOpenCb(const char* fname, int32_t* pSize) {
  g_file = LittleFS.open(fname, "r");
  if (!g_file) return nullptr;
  *pSize = g_file.size();
  return (void*)&g_file;
}
void gifCloseCb(void* h) {
  File* f = (File*)h;
  if (f) f->close();
}
int32_t gifReadCb(GIFFILE* pFile, uint8_t* pBuf, int32_t iLen) {
  File* f = (File*)pFile->fHandle;
  int32_t n = f->read(pBuf, iLen);
  pFile->iPos = f->position();
  return n;
}
int32_t gifSeekCb(GIFFILE* pFile, int32_t iPosition) {
  File* f = (File*)pFile->fHandle;
  f->seek(iPosition);
  pFile->iPos = (int32_t)f->position();
  return pFile->iPos;
}

// --- Per-scanline draw callback --------------------------------------------
// Horizontal bilinear + vertical nearest-neighbor scaling. The GIF lib
// hands us one source row at a time, so true vertical bilinear would
// need cross-row buffering that breaks on GIFs' partial-row frame
// updates (animation disposal). Horizontal bilinear is stateless and
// removes the most visible artifact — horizontal stair-stepping — at
// the ~1.0-1.5x upscale this layout uses. Vertical stays NN: the
// output row range a source row covers is just replicated.
void gifDrawCb(GIFDRAW* d) {
  uint16_t* pal  = d->pPalette;
  uint8_t*  src  = d->pPixels;
  uint8_t   tc   = d->ucTransparent;
  bool      hasT = d->ucHasTransparency;

  int srcY = d->iY + d->y;
  int srcW = d->iWidth;

  // Map this 1px-tall source row to a range of output rows.
  int out_y0 = (int)(srcY       * g_scale_f);
  int out_y1 = (int)((srcY + 1) * g_scale_f);
  if (out_y1 <= out_y0) out_y1 = out_y0 + 1;

  // Map source X to output X.
  int out_x0  = (int)(d->iX     * g_scale_f);
  int out_x1  = (int)((d->iX + srcW) * g_scale_f);
  int out_w   = out_x1 - out_x0;
  if (out_w <= 0) return;
  if (out_w > (int)(sizeof(g_line) / sizeof(g_line[0]))) {
    out_w = sizeof(g_line) / sizeof(g_line[0]);
  }

  // Build the scaled output row — horizontal bilinear. For each output
  // x, find the fractional source x, blend the two straddling source
  // pixels. Transparent source pixels resolve to g_bg before blending,
  // so character edges soften against the background instead of
  // hard-stepping. inv_scale precomputed to avoid a divide per pixel.
  float inv_scale = 1.0f / g_scale_f;
  for (int xo = 0; xo < out_w; xo++) {
    float    sx   = xo * inv_scale;
    int      x0   = (int)sx;
    int      x1   = x0 + 1;
    int      frac = (int)((sx - x0) * 256.0f);
    if (x0 >= srcW) x0 = srcW - 1;
    if (x1 >= srcW) x1 = srcW - 1;
    uint16_t c0 = (hasT && src[x0] == tc) ? g_bg : pal[src[x0]];
    uint16_t c1 = (hasT && src[x1] == tc) ? g_bg : pal[src[x1]];
    g_line[xo] = (c0 == c1) ? c0 : blend565(c0, c1, frac);
  }

  // Clip character draws to the CHAR_BOX region — stats bar at the
  // bottom and text panel to the right must not get overwritten.
  int max_y    = CHAR_BOX_Y + CHAR_BOX_H;
  int max_x    = CHAR_BOX_X + CHAR_BOX_W;
  int x_dst    = g_gx + out_x0;
  int draw_w   = out_w;
  if (x_dst < CHAR_BOX_X) { draw_w -= (CHAR_BOX_X - x_dst); x_dst = CHAR_BOX_X; }
  if (x_dst + draw_w > max_x) draw_w = max_x - x_dst;
  if (draw_w <= 0) return;

  for (int y = out_y0; y < out_y1; y++) {
    int abs_y = g_gy + y;
    if (abs_y < 0 || abs_y >= max_y) continue;
    M5.Lcd.pushImage(x_dst, abs_y, draw_w, 1, g_line);
  }
}

// --- GIF open / placement --------------------------------------------------
void closeCurrentGif() {
  if (g_gif_open) {
    g_gif.close();
    g_gif_open = false;
  }
}

// clear_canvas = true → fillRect the upper region before opening.
// Needed when switching to a different-sized GIF (different state).
// Skipped on same-GIF loop restart to avoid the per-1.3s screen flash.
bool openStateGif(uint8_t state, bool clear_canvas) {
  if (state >= CHAR_N_STATES) return false;

  const char* fname = STATE_FILES[state];
  char var_buf[20];
  // Some states have multiple GIF variants picked at random for variety:
  //   busy      → busy_0..busy_3  (busy_3 = the "speaking" claude anim)
  //   celebrate → celebrate.gif + celebrate_1.gif ("jumping" claude anim)
  // Other states use STATE_FILES[state] verbatim.
  if (state == CHAR_BUSY) {
    snprintf(var_buf, sizeof(var_buf), "busy_%u.gif", (unsigned)(esp_random() % 4));
    fname = var_buf;
  } else if (state == CHAR_CELEBRATE && (esp_random() & 1)) {
    fname = "celebrate_1.gif";
  }
  snprintf(g_full_path, sizeof(g_full_path), "%s/%s", g_base, fname);

  closeCurrentGif();

  if (clear_canvas) {
    // Clear only the CHAR_BOX — text panel + stats bar are owned by
    // paintStatusBarIfChanged and shouldn't be repainted from here.
    M5.Lcd.fillRect(CHAR_BOX_X, CHAR_BOX_Y, CHAR_BOX_W, CHAR_BOX_H, g_bg);
  }

  if (!g_gif.open(g_full_path, gifOpenCb, gifCloseCb,
                  gifReadCb, gifSeekCb, gifDrawCb)) {
    Serial.printf("[char] gif open failed: %s (err=%d)\n",
                  g_full_path, g_gif.getLastError());
    return false;
  }
  g_gif_open = true;

  g_src_w = g_gif.getCanvasWidth();
  g_src_h = g_gif.getCanvasHeight();

  // Fit-into-box: pick scale so the GIF fills CHAR_BOX without bleeding
  // out either dimension. min(scale_w, scale_h) keeps aspect; floor at
  // 0.4 just in case a tiny GIF would otherwise shrink to nothing.
  float scale_w = (float)CHAR_BOX_W / (float)g_src_w;
  float scale_h = (float)CHAR_BOX_H / (float)g_src_h;
  g_scale_f = scale_w < scale_h ? scale_w : scale_h;
  if (g_scale_f < 0.4f) g_scale_f = 0.4f;
  g_out_w = (int)(g_src_w * g_scale_f);
  g_out_h = (int)(g_src_h * g_scale_f);
  // Center within CHAR_BOX.
  g_gx = CHAR_BOX_X + (CHAR_BOX_W - g_out_w) / 2;
  g_gy = CHAR_BOX_Y + (CHAR_BOX_H - g_out_h) / 2;

  Serial.printf("[char] opened %s  src=%dx%d × %.2f → %dx%d @ (%d,%d)\n",
                g_full_path, g_src_w, g_src_h, g_scale_f,
                g_out_w, g_out_h, g_gx, g_gy);
  g_next_frame_at = 0;
  return true;
}

// Word-wrap text into the given pixel-width box. Breaks at whitespace
// or punctuation (_ - : .) when possible; falls back to hard char-break
// for unbroken Claude-Code tool names like
// `mcp__plugin_context-mode_context-mode_ctx_search`. Caller must have
// set the font/color/datum before invoking. max_lines caps output so
// runaway msgs don't paint over the stats bar.
void drawWrapped(const char* text, int x, int y, int max_w,
                 int line_h, int max_lines) {
  if (!text || !*text || max_lines <= 0) return;
  char line[80];
  size_t llen = 0;
  int cur_y = y;
  int drawn = 0;

  auto flush_at = [&](size_t break_at) {
    char saved = line[break_at];
    line[break_at] = 0;
    M5.Lcd.drawString(line, x, cur_y);
    line[break_at] = saved;
    cur_y += line_h;
    drawn++;
    size_t rem = llen - break_at;
    memmove(line, line + break_at, rem);
    llen = rem;
    while (llen > 0 && (line[0] == ' ')) {
      memmove(line, line + 1, llen);
      llen--;
    }
    line[llen] = 0;
  };

  for (const char* p = text; *p && drawn < max_lines; p++) {
    if (llen >= sizeof(line) - 1) flush_at(llen);
    line[llen++] = *p;
    line[llen] = 0;
    if (M5.Lcd.textWidth(line) > max_w) {
      // Backtrack to last break-candidate char.
      int b = (int)llen - 1;
      while (b > 0) {
        char c = line[b];
        if (c == ' ' || c == '_' || c == '-' || c == ':' || c == '.') break;
        b--;
      }
      if (b == 0) b = (int)llen - 1;  // hard break — single long token
      flush_at((size_t)(b + 1));
      if (drawn >= max_lines) return;
    }
  }
  if (llen > 0 && drawn < max_lines) {
    line[llen] = 0;
    M5.Lcd.drawString(line, x, cur_y);
  }
}

// --- Status paint -----------------------------------------------------------
// Three regions, each repainted lazily on dirty check:
//   HUD       — top 20px strip, R/W/tokens, FreeSans9pt light grey
//   BUBBLE    — right speech bubble, msg word-wrap, FreeSansBold9pt,
//               border colour driven by current state
//   TOOL_CHIP — below bubble, orange pill with current tool name
// border state dirty bit tracks the last colour painted so a state
// change repaints the bubble outline without forcing a full text
// repaint.
uint8_t      g_accent_drawn = 0xFF;   // last state painted to bubble header

// Animal-Crossing card primitive: a warm cream surface with a 2px
// border and the signature flat offset drop-shadow (box-shadow:
// 0 Npx 0 0 — no blur). Painted as three stacked smooth rounded
// rects: shadow (offset down), border, then the fill inset by the
// border width. Clears its own footprint (card + shadow) to the dark
// canvas first so repaints don't leave halos.
void drawAcnhCard(int x, int y, int w, int h, int r, uint16_t fill) {
  M5.Lcd.fillRect(x, y, w, h + CARD_SHADOW_DY, SCREEN_BG);
  M5.Lcd.fillSmoothRoundRect(x, y + CARD_SHADOW_DY, w, h, r, CARD_SHADOW);
  M5.Lcd.fillSmoothRoundRect(x, y, w, h, r, CARD_BORDER);
  M5.Lcd.fillSmoothRoundRect(x + CARD_BW, y + CARD_BW,
                             w - 2 * CARD_BW, h - 2 * CARD_BW,
                             r - CARD_BW, fill);
}

// Bubble = an ACNH cream card with an accent header strip (state
// colour) carrying the CAPS state label, msg body below. Header sits
// inside the 2px border, rounded at top to match, square at bottom.
void drawBubbleCard(uint8_t state) {
  uint16_t accent = accentForState(state);
  drawAcnhCard(BUBBLE_X, BUBBLE_Y, BUBBLE_W, BUBBLE_H, BUBBLE_R, CARD_FILL);

  int sx = BUBBLE_X + CARD_BW;
  int sy = BUBBLE_Y + CARD_BW;
  int sw = BUBBLE_W - 2 * CARD_BW;
  int sr = BUBBLE_R - CARD_BW;
  M5.Lcd.fillSmoothRoundRect(sx, sy, sw, BUBBLE_HEAD_H, sr, accent);
  M5.Lcd.fillRect(sx, sy + sr, sw, BUBBLE_HEAD_H - sr, accent);
  // Divider between the strip and the body.
  M5.Lcd.drawFastHLine(sx, sy + BUBBLE_HEAD_H, sw, CARD_DIV);

  // CAPS state label — white on saturated accents, brown on yellow.
  M5.Lcd.setTextColor(headerTextForState(state), accent);
  M5.Lcd.setTextDatum(middle_left);
  M5.Lcd.setTextSize(1);
  M5.Lcd.setFont(&fonts::FreeSansBold9pt7b);
  M5.Lcd.drawString(labelForState(state),
                    sx + 8, sy + BUBBLE_HEAD_H / 2);
}

// Compact token count: "850", "48.0k", "1.2M".
void fmtTokens(uint32_t t, char* buf, size_t n) {
  if (t >= 1000000) {
    snprintf(buf, n, "%lu.%luM", (unsigned long)(t / 1000000),
             (unsigned long)((t / 100000) % 10));
  } else if (t >= 1000) {
    snprintf(buf, n, "%lu.%luk", (unsigned long)(t / 1000),
             (unsigned long)((t / 100) % 10));
  } else {
    snprintf(buf, n, "%lu", (unsigned long)t);
  }
}

// Session elapsed: "45s", "12m", "1h23m".
void fmtDuration(uint32_t ms, char* buf, size_t n) {
  uint32_t s = ms / 1000;
  if (s < 60)        snprintf(buf, n, "%lus", (unsigned long)s);
  else if (s < 3600) snprintf(buf, n, "%lum", (unsigned long)(s / 60));
  else snprintf(buf, n, "%luh%lum", (unsigned long)(s / 3600),
                (unsigned long)((s % 3600) / 60));
}

// HUD = a 2-row ACNH cream card spanning the top, carrying the live
// OMC-HUD metrics (openspec 0002):
//   row 1:  <model>                              <context%> ctx
//   row 2:  <session> · <tokens> tok       5h <n>%  7d <n>%
// All from the cc-bridge `hud` event; warm brown text on cream.
void drawHud() {
  int x = 4;
  int w = M5.Lcd.width() - 8;
  drawAcnhCard(x, HUD_Y, w, HUD_H, 16, CARD_FILL);

  int pad    = CARD_BW + 10;
  int row1_y = HUD_Y + 15;
  int row2_y = HUD_Y + 35;
  M5.Lcd.setTextSize(1);
  M5.Lcd.setFont(&fonts::FreeSansBold9pt7b);

  // Row 1 left — model (truncated to the left half).
  char model[24];
  strncpy(model, g_model[0] ? g_model : "—", sizeof(model) - 1);
  model[sizeof(model) - 1] = 0;
  M5.Lcd.setTextColor(CARD_TEXT, CARD_FILL);
  M5.Lcd.setTextDatum(middle_left);
  int model_max = w / 2;
  while (M5.Lcd.textWidth(model) > model_max && strlen(model) > 1) {
    model[strlen(model) - 1] = 0;
  }
  M5.Lcd.drawString(model, x + pad, row1_y);

  // Row 1 right — context window %.
  char ctx[16];
  snprintf(ctx, sizeof(ctx), "%d%% ctx", g_context_pct);
  M5.Lcd.setTextDatum(middle_right);
  M5.Lcd.drawString(ctx, x + w - pad, row1_y);

  // Row 2 left — session elapsed · token count.
  char dur[12], tok[12], l2[32];
  fmtDuration(g_session_ms, dur, sizeof(dur));
  fmtTokens(g_hud_tokens, tok, sizeof(tok));
  snprintf(l2, sizeof(l2), "%s  %s tok", dur, tok);
  M5.Lcd.setTextColor(CARD_TEXT_SEC, CARD_FILL);
  M5.Lcd.setTextDatum(middle_left);
  M5.Lcd.drawString(l2, x + pad, row2_y);

  // Row 2 right — rolling rate-limit pressure.
  char lim[24];
  snprintf(lim, sizeof(lim), "5h %d%%  7d %d%%", g_limit_5h, g_limit_7d);
  M5.Lcd.setTextDatum(middle_right);
  M5.Lcd.drawString(lim, x + w - pad, row2_y);
}

// Tool chip = a small ACNH cream pill with a leading accent dot and
// the uppercased tool name in warm brown.
void drawToolChip(uint16_t accent) {
  if (!g_tool[0]) {
    // Clear the chip footprint (incl. shadow) back to the canvas.
    M5.Lcd.fillRect(TOOL_CHIP_X, TOOL_CHIP_Y,
                    TOOL_CHIP_W, TOOL_CHIP_H + CARD_SHADOW_DY, SCREEN_BG);
    return;
  }
  drawAcnhCard(TOOL_CHIP_X, TOOL_CHIP_Y, TOOL_CHIP_W, TOOL_CHIP_H,
               TOOL_CHIP_R, CARD_FILL);

  int dot_cx = TOOL_CHIP_X + CARD_BW + 12;
  int dot_cy = TOOL_CHIP_Y + TOOL_CHIP_H / 2;
  M5.Lcd.fillSmoothCircle(dot_cx, dot_cy, 4, accent);

  // Uppercase the tool name.
  char buf[24];
  size_t n = 0;
  for (const char* p = g_tool; *p && n < sizeof(buf) - 1; p++, n++) {
    char c = *p;
    if (c >= 'a' && c <= 'z') c -= 32;
    buf[n] = c;
  }
  buf[n] = 0;

  M5.Lcd.setTextColor(CARD_TEXT, CARD_FILL);
  M5.Lcd.setTextDatum(middle_left);
  M5.Lcd.setTextSize(1);
  M5.Lcd.setFont(&fonts::FreeSansBold9pt7b);
  int text_x     = dot_cx + 10;
  int text_max_w = TOOL_CHIP_X + TOOL_CHIP_W - CARD_BW - 10 - text_x;
  while (M5.Lcd.textWidth(buf) > text_max_w && strlen(buf) > 1) {
    size_t L = strlen(buf);
    buf[L - 1] = 0;
    if (L >= 2) buf[L - 2] = '.';
    if (L >= 3) buf[L - 3] = '.';
  }
  M5.Lcd.drawString(buf, text_x, TOOL_CHIP_Y + TOOL_CHIP_H / 2);
}

void paintStatusBarIfChanged() {
  bool    msg_dirty    = (strncmp(g_msg, g_msg_drawn, sizeof(g_msg)) != 0);
  bool    accent_dirty = (g_cur_state != g_accent_drawn);
  uint16_t accent      = accentForState(g_cur_state);

  // Dirty key for the tool chip (g_tool) + the HUD card metrics. R/W
  // counters drive the bubble state, not the HUD, so they're not keyed
  // here — accent_dirty already covers state changes.
  char combined[128];
  snprintf(combined, sizeof(combined), "%s|%d|%s|%lu|%lu|%d|%d|%lu",
           g_tool, g_context_pct, g_model, (unsigned long)g_hud_tokens,
           (unsigned long)g_session_ms, g_limit_5h, g_limit_7d,
           (unsigned long)g_tokens);
  bool stats_dirty = (strncmp(combined, g_stats_drawn,
                              sizeof(g_stats_drawn)) != 0);

  if (!msg_dirty && !stats_dirty && !accent_dirty) return;

  if (msg_dirty || accent_dirty) {
    drawBubbleCard(g_cur_state);
    M5.Lcd.setTextColor(CARD_TEXT, CARD_FILL);
    M5.Lcd.setTextDatum(top_left);
    M5.Lcd.setTextSize(1);
    M5.Lcd.setFont(&fonts::FreeSansBold9pt7b);
    // Body area starts below the header strip + border inset.
    int body_y = BUBBLE_Y + CARD_BW + BUBBLE_HEAD_H + BUBBLE_PAD;
    int body_h = BUBBLE_H - CARD_BW - BUBBLE_HEAD_H - 2 * BUBBLE_PAD;
    int max_lines = body_h / 16;   // line_h=16 → 7 lines in 112px
    drawWrapped(g_msg,
                BUBBLE_X + BUBBLE_PAD, body_y,
                BUBBLE_W - 2 * BUBBLE_PAD,
                /*line_h=*/16, max_lines);
    strncpy(g_msg_drawn, g_msg, sizeof(g_msg_drawn) - 1);
    g_msg_drawn[sizeof(g_msg_drawn) - 1] = 0;
  }

  if (stats_dirty || accent_dirty) {
    drawHud();
    drawToolChip(accent);
    strncpy(g_stats_drawn, combined, sizeof(g_stats_drawn) - 1);
    g_stats_drawn[sizeof(g_stats_drawn) - 1] = 0;
  }

  g_accent_drawn = g_cur_state;
  M5.Lcd.setFont(&fonts::Font0);
}

}  // namespace

// ===========================================================================
// Public API
// ===========================================================================
bool characterInit(const char* name) {
  if (!LittleFS.begin(false)) {
    Serial.println("[char] LittleFS mount failed; trying format-on-fail");
    if (!LittleFS.begin(true)) {
      Serial.println("[char] LittleFS still failing — bailing");
      return false;
    }
  }

  char auto_buf[24];
  if (!name) {
    File root = LittleFS.open("/characters");
    if (root && root.isDirectory()) {
      File e = root.openNextFile();
      while (e) {
        if (e.isDirectory()) {
          const char* slash = strrchr(e.name(), '/');
          strncpy(auto_buf, slash ? slash + 1 : e.name(),
                  sizeof(auto_buf) - 1);
          auto_buf[sizeof(auto_buf) - 1] = 0;
          name = auto_buf;
          break;
        }
        e = root.openNextFile();
      }
    }
    if (!name) {
      Serial.println("[char] no /characters/* on LittleFS");
      return false;
    }
  }

  snprintf(g_base, sizeof(g_base), "/characters/%s", name);
  Serial.printf("[char] base=%s\n", g_base);

  char manifest_path[80];
  snprintf(manifest_path, sizeof(manifest_path), "%s/manifest.json", g_base);
  File mf = LittleFS.open(manifest_path, "r");
  if (mf) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, mf);
    if (!err) {
      const char* bgs = doc["colors"]["bg"] | "#000000";
      g_bg = parseHexColor(bgs, 0x0000);
      Serial.printf("[char] manifest bg=%s → 0x%04x\n", bgs, g_bg);
    }
    mf.close();
  }
  // Force black canvas for the watchOS look. g_bg still drives GIF
  // transparency inside CHAR_BOX, but the screen padding is always
  // pure black so the dark cards float on it.
  g_bg = SCREEN_BG;

  M5.Lcd.setRotation(1);
  M5.Lcd.fillScreen(SCREEN_BG);

  g_gif.begin(GIF_PALETTE_RGB565_BE);
  return true;
}

void characterSetState(uint8_t state) {
  if (state >= CHAR_N_STATES) return;
  bool same_state = (state == g_cur_state);
  if (same_state && state != CHAR_BUSY) return;
  g_cur_state = state;
  openStateGif(state, !same_state);
}

void characterTick() {
  paintStatusBarIfChanged();

  if (!g_gif_open) return;
  uint32_t now = millis();
  if (now < g_next_frame_at) return;

  int delayMs = 0;
  int rc = g_gif.playFrame(false, &delayMs);
  if (rc == 0) {
    openStateGif(g_cur_state, false);   // see openStateGif() comment
    g_next_frame_at = now + 20;
    return;
  }
  if (rc < 0) {
    Serial.printf("[char] playFrame err=%d\n", g_gif.getLastError());
    closeCurrentGif();
    return;
  }
  if (delayMs < 16) delayMs = 16;
  g_next_frame_at = now + delayMs;
}

void characterReload(const char* name) {
  // Close current GIF so the next openStateGif gets a clean slate.
  // characterInit re-mounts LittleFS (no-op since already mounted),
  // sets g_base to the new path, refreshes bg color from manifest.
  // We then force state change by clearing g_cur_state so the next
  // characterSetState reloads the GIF even if the state matches.
  // Skip if name is the same — avoids unnecessary flicker.
  char want[48];
  snprintf(want, sizeof(want), "/characters/%s", name ? name : "");
  if (name && strcmp(g_base, want) == 0) {
    Serial.printf("[char] reload skipped: already on %s\n", g_base);
    return;
  }
  // Stop any in-flight GIF before changing g_base.
  if (g_gif_open) {
    g_gif.close();
    g_gif_open = false;
  }
  uint8_t was = g_cur_state;
  g_cur_state = 0xFF;
  if (!characterInit(name)) {
    Serial.println("[char] reload init failed");
    return;
  }
  if (was < CHAR_N_STATES) {
    characterSetState(was);
  } else {
    characterSetState(CHAR_SLEEP);
  }
}

void characterSetMsg(const char* msg) {
  if (!msg) msg = "";
  strncpy(g_msg, msg, sizeof(g_msg) - 1);
  g_msg[sizeof(g_msg) - 1] = 0;
}

void characterSetStats(int running, int waiting, uint32_t tokens, const char* tool) {
  g_running = running;
  g_waiting = waiting;
  g_tokens  = tokens;
  if (!tool) tool = "";
  strncpy(g_tool, tool, sizeof(g_tool) - 1);
  g_tool[sizeof(g_tool) - 1] = 0;
}

void characterSetHud(int context_pct, const char* model, uint32_t tokens,
                     int limit_5h, int limit_7d, uint32_t session_ms) {
  g_context_pct = context_pct;
  if (!model) model = "";
  strncpy(g_model, model, sizeof(g_model) - 1);
  g_model[sizeof(g_model) - 1] = 0;
  g_hud_tokens = tokens;
  g_limit_5h   = limit_5h;
  g_limit_7d   = limit_7d;
  g_session_ms = session_ms;
}
