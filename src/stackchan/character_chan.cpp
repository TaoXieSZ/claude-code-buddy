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
constexpr int  STATUS_BAR_H = 60;   // bottom strip for msg + stats
constexpr int  STATUS_PAD_X = 4;
constexpr int  TARGET_H     = 170;  // uniform character height (px)

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

  int lcd_h    = M5.Lcd.height();
  int max_y    = lcd_h - STATUS_BAR_H;
  int x_dst    = g_gx + out_x0;
  int lcd_w    = M5.Lcd.width();
  int draw_w   = out_w;
  if (x_dst < 0) { draw_w += x_dst; x_dst = 0; }
  if (x_dst + draw_w > lcd_w) draw_w = lcd_w - x_dst;
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
    M5.Lcd.fillRect(0, 0, M5.Lcd.width(),
                    M5.Lcd.height() - STATUS_BAR_H, g_bg);
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

  // Uniform target height: scale so every GIF ends up TARGET_H pixels
  // tall regardless of native dimensions. Width follows aspect.
  g_scale_f = (float)TARGET_H / (float)g_src_h;
  g_out_w   = (int)(g_src_w * g_scale_f);
  g_out_h   = TARGET_H;

  int lcd_w   = M5.Lcd.width();
  int avail_h = M5.Lcd.height() - STATUS_BAR_H;
  g_gx = (lcd_w - g_out_w) / 2;
  g_gy = (avail_h - g_out_h) / 2;
  if (g_gy < 0) g_gy = 0;

  Serial.printf("[char] opened %s  src=%dx%d × %.2f → %dx%d @ (%d,%d)\n",
                g_full_path, g_src_w, g_src_h, g_scale_f,
                g_out_w, g_out_h, g_gx, g_gy);
  g_next_frame_at = 0;
  return true;
}

// --- Status bar paint ------------------------------------------------------
// Bar layout (60 px tall, bottom of LCD):
//   y = bar_y      → top edge
//   y = bar_y + 8  → "msg" row baseline (size 2 text, 16px tall)
//   y = bar_y + 32 → "stats" row baseline (size 1 text, 8px tall)
//   y = bar_y + 48 → "tool" row baseline (size 1)
void paintStatusBarIfChanged() {
  int lcd_w = M5.Lcd.width();
  int lcd_h = M5.Lcd.height();
  int bar_y = lcd_h - STATUS_BAR_H;

  bool msg_dirty  = (strncmp(g_msg, g_msg_drawn, sizeof(g_msg)) != 0);

  // Build the stats string into a stable buffer.
  char stats_now[64];
  if (g_tokens >= 1000) {
    snprintf(stats_now, sizeof(stats_now), "R:%d W:%d  tok:%lu.%luk%s%s",
             g_running, g_waiting,
             (unsigned long)(g_tokens / 1000),
             (unsigned long)((g_tokens / 100) % 10),
             g_tool[0] ? "  " : "",
             g_tool);
  } else {
    snprintf(stats_now, sizeof(stats_now), "R:%d W:%d  tok:%lu%s%s",
             g_running, g_waiting, (unsigned long)g_tokens,
             g_tool[0] ? "  " : "",
             g_tool);
  }
  bool stats_dirty = (strncmp(stats_now, g_stats_drawn, sizeof(g_stats_drawn)) != 0);

  if (!msg_dirty && !stats_dirty) return;

  if (msg_dirty) {
    // Clear msg row only — keep stats row intact when only msg changed.
    M5.Lcd.fillRect(0, bar_y, lcd_w, 28, g_bg);
    M5.Lcd.setTextColor(TFT_WHITE, g_bg);
    M5.Lcd.setTextDatum(top_left);
    M5.Lcd.setTextSize(2);
    char buf[40];
    size_t n = strnlen(g_msg, sizeof(g_msg) - 1);
    if (n > 26) n = 26;
    memcpy(buf, g_msg, n);
    buf[n] = 0;
    M5.Lcd.drawString(buf, STATUS_PAD_X, bar_y + 4);
    strncpy(g_msg_drawn, g_msg, sizeof(g_msg_drawn) - 1);
    g_msg_drawn[sizeof(g_msg_drawn) - 1] = 0;
  }

  if (stats_dirty) {
    M5.Lcd.fillRect(0, bar_y + 30, lcd_w, STATUS_BAR_H - 30, g_bg);
    M5.Lcd.setTextColor(TFT_LIGHTGREY, g_bg);
    M5.Lcd.setTextDatum(top_left);
    M5.Lcd.setTextSize(1);
    M5.Lcd.drawString(stats_now, STATUS_PAD_X, bar_y + 34);
    strncpy(g_stats_drawn, stats_now, sizeof(g_stats_drawn) - 1);
    g_stats_drawn[sizeof(g_stats_drawn) - 1] = 0;
  }
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
