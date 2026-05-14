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
#include <M5Unified.h>
#include <LittleFS.h>
#include <AnimatedGIF.h>
#include <ArduinoJson.h>

namespace {

// --- Geometry --------------------------------------------------------------
// 2026-05-14 layout — character LEFT, msg panel RIGHT, stats BOTTOM:
//   +----------------+--------------------+
//   | character box  | msg word-wrap      |
//   | 130 × 144      | 170 × 156          |
//   +----------------+--------------------+
//   |  stats bar (full width, 72 tall)    |
//   +-------------------------------------+
// Pre-redesign the character was centered at TARGET_H=170 and left
// wide bands of empty pixels on both sides; msg was a single clipped
// line. Now those sides are an actual text panel with word-wrap.
constexpr int  CHAR_BOX_X    = 8;
constexpr int  CHAR_BOX_Y    = 12;
constexpr int  CHAR_BOX_W    = 130;
constexpr int  CHAR_BOX_H    = 144;
constexpr int  TEXT_PANEL_X  = 146;
constexpr int  TEXT_PANEL_Y  = 10;
constexpr int  TEXT_PANEL_W  = 170;   // 320 - 146 - 4 right margin
constexpr int  TEXT_PANEL_H  = 152;
constexpr int  STATS_BAR_Y   = 168;
constexpr int  STATS_BAR_H   = 72;    // ends at y=240
constexpr int  STATUS_PAD_X  = 6;

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
char         g_stats_drawn[64] = "";   // last rendered stats line

// Scanline buffer — sized for max output width at TARGET_H scale. The
// largest output width is sleep.gif/busy.gif at aspect ratio ~120:118
// scaled to height 170 → ~172 wide. 360 is comfortable headroom.
uint16_t     g_line[360];

// --- Helpers ---------------------------------------------------------------
uint16_t parseHexColor(const char* s, uint16_t fb) {
  if (!s || !*s) return fb;
  if (*s == '#') s++;
  uint32_t v = strtoul(s, nullptr, 16);
  return (uint16_t)(((v >> 19) & 0x1F) << 11 |
                    ((v >> 10) & 0x3F) << 5  |
                    ((v >> 3)  & 0x1F));
}

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
// Nearest-neighbor float scaling: for each source row, compute the
// output Y range it covers and the doubled-width output row. Push each
// covered output row to LCD via pushImage (line buffer).
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

  // Build the scaled output row (NN sample from src).
  for (int xo = 0; xo < out_w; xo++) {
    int xi = (int)(xo / g_scale_f);
    if (xi >= srcW) xi = srcW - 1;
    g_line[xo] = (hasT && src[xi] == tc) ? g_bg : pal[src[xi]];
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
  char busy_buf[16];
  if (state == CHAR_BUSY) {
    snprintf(busy_buf, sizeof(busy_buf), "busy_%u.gif", (unsigned)(esp_random() % 3));
    fname = busy_buf;
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
// Two regions, each repainted lazily on dirty check:
//   TEXT_PANEL — right side, msg with word-wrap, FreeSansBold12pt
//   STATS_BAR  — bottom, R/W/tokens + active tool, FreeSans12pt centered
void paintStatusBarIfChanged() {
  bool msg_dirty = (strncmp(g_msg, g_msg_drawn, sizeof(g_msg)) != 0);

  // Build the stats string into a stable buffer.
  char stats_now[80];
  if (g_tokens >= 1000) {
    snprintf(stats_now, sizeof(stats_now), "R:%d  W:%d  tok:%lu.%luk%s%s",
             g_running, g_waiting,
             (unsigned long)(g_tokens / 1000),
             (unsigned long)((g_tokens / 100) % 10),
             g_tool[0] ? "  " : "",
             g_tool);
  } else {
    snprintf(stats_now, sizeof(stats_now), "R:%d  W:%d  tok:%lu%s%s",
             g_running, g_waiting, (unsigned long)g_tokens,
             g_tool[0] ? "  " : "",
             g_tool);
  }
  bool stats_dirty = (strncmp(stats_now, g_stats_drawn, sizeof(g_stats_drawn)) != 0);

  if (!msg_dirty && !stats_dirty) return;

  if (msg_dirty) {
    M5.Lcd.fillRect(TEXT_PANEL_X, TEXT_PANEL_Y,
                    TEXT_PANEL_W, TEXT_PANEL_H, g_bg);
    M5.Lcd.setTextColor(TFT_WHITE, g_bg);
    M5.Lcd.setTextDatum(top_left);
    M5.Lcd.setTextSize(1);
    M5.Lcd.setFont(&fonts::FreeSansBold12pt7b);
    // 22 px per line accommodates the 18 px FreeSansBold12pt7b glyph
    // box plus ~4 px of leading. 6 lines × 22 = 132 px, fits inside
    // TEXT_PANEL_H=152.
    drawWrapped(g_msg, TEXT_PANEL_X + 2, TEXT_PANEL_Y + 2,
                TEXT_PANEL_W - 4, /*line_h=*/22, /*max_lines=*/6);
    strncpy(g_msg_drawn, g_msg, sizeof(g_msg_drawn) - 1);
    g_msg_drawn[sizeof(g_msg_drawn) - 1] = 0;
  }

  if (stats_dirty) {
    M5.Lcd.fillRect(0, STATS_BAR_Y, M5.Lcd.width(), STATS_BAR_H, g_bg);
    // Thin top divider so the stats bar reads as a distinct region.
    M5.Lcd.drawFastHLine(0, STATS_BAR_Y, M5.Lcd.width(), TFT_DARKGREY);
    M5.Lcd.setTextColor(TFT_LIGHTGREY, g_bg);
    M5.Lcd.setTextDatum(middle_left);
    M5.Lcd.setTextSize(1);
    M5.Lcd.setFont(&fonts::FreeSans12pt7b);
    M5.Lcd.drawString(stats_now, STATUS_PAD_X,
                      STATS_BAR_Y + STATS_BAR_H / 2);
    strncpy(g_stats_drawn, stats_now, sizeof(g_stats_drawn) - 1);
    g_stats_drawn[sizeof(g_stats_drawn) - 1] = 0;
  }

  // Restore default font so any drawString elsewhere doesn't inherit.
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

  M5.Lcd.setRotation(1);
  M5.Lcd.fillScreen(g_bg);

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
