// clawd GIF avatar — AnimatedGIF + LittleFS, scanline scaling into an
// offscreen canvas. Decode pipeline copied from the proven CoreS3 stackchan
// renderer (character_chan.cpp): GIF_PALETTE_RGB565_BE palette, per-row
// pushImage, horizontal nearest scaling (the box is small enough that
// bilinear isn't worth the cycles here).
#include <M5Unified.h>
#include <LittleFS.h>
#include <AnimatedGIF.h>
#include "avatar.h"

static constexpr int BOX = 220;        // canvas + max output size
static const char* PACK = "/characters/clawd/";

// ui.cpp AState order: IDLE, BUSY, ATTN, DONE, ERR
static const char* STATE_FILES[5] = {
  "idle.gif", "busy_0.gif", "attention.gif", "celebrate.gif", "dizzy.gif",
};

static AnimatedGIF g_gif;
static File        g_file;
static M5Canvas    g_cv;
static bool        g_ready = false;
static bool        g_open = false;
static uint8_t     g_state = 0xFF;
static uint16_t    g_bg = 0;          // GIF backdrop (pack bg = black)
static uint16_t    g_panel = 0;       // sidebar color, for the corner carve
static uint16_t    g_border = 0;
static float       g_scale = 1.0f;
static int         g_ox = 0, g_oy = 0;     // centering offset inside canvas
static uint32_t    g_nextFrame = 0;
static uint16_t    g_line[BOX];
static void applyChrome();

static void* cbOpen(const char* fname, int32_t* size) {
  g_file = LittleFS.open(fname, "r");
  if (!g_file) return nullptr;
  *size = g_file.size();
  return (void*)&g_file;
}
static void cbClose(void* h) { File* f = (File*)h; if (f) f->close(); }
static int32_t cbRead(GIFFILE* pf, uint8_t* buf, int32_t len) {
  File* f = (File*)pf->fHandle;
  int32_t n = f->read(buf, len);
  pf->iPos = f->position();
  return n;
}
static int32_t cbSeek(GIFFILE* pf, int32_t pos) {
  File* f = (File*)pf->fHandle;
  f->seek(pos);
  pf->iPos = (int32_t)f->position();
  return pf->iPos;
}

static void cbDraw(GIFDRAW* d) {
  uint16_t* pal = d->pPalette;
  uint8_t*  src = d->pPixels;
  uint8_t   tc  = d->ucTransparent;
  bool      hasT = d->ucHasTransparency;

  int srcY = d->iY + d->y;
  int srcW = d->iWidth;
  int oy0 = (int)(srcY * g_scale);
  int oy1 = (int)((srcY + 1) * g_scale);
  if (oy1 <= oy0) oy1 = oy0 + 1;
  int ox0 = (int)(d->iX * g_scale);
  int ox1 = (int)((d->iX + srcW) * g_scale);
  int ow = ox1 - ox0;
  if (ow <= 0) return;
  if (ow > BOX) ow = BOX;

  float inv = 1.0f / g_scale;
  for (int xo = 0; xo < ow; xo++) {
    int sx = (int)(xo * inv);
    if (sx >= srcW) sx = srcW - 1;
    g_line[xo] = (hasT && src[sx] == tc) ? g_bg : pal[src[sx]];
  }
  for (int y = oy0; y < oy1; y++) {
    int yy = g_oy + y;
    if (yy < 0 || yy >= BOX) continue;
    g_cv.pushImage(g_ox + ox0, yy, ow, 1, g_line);
  }
}

static bool openState(uint8_t st) {
  if (g_open) { g_gif.close(); g_open = false; }
  char path[80];
  snprintf(path, sizeof(path), "%s%s", PACK, STATE_FILES[st < 5 ? st : 0]);
  if (!g_gif.open(path, cbOpen, cbClose, cbRead, cbSeek, cbDraw)) {
    Serial.printf("[avatar] open failed: %s (err=%d)\n", path, g_gif.getLastError());
    return false;
  }
  g_open = true;
  int w = g_gif.getCanvasWidth(), h = g_gif.getCanvasHeight();
  float sx = (float)BOX / w, sy = (float)BOX / h;
  g_scale = sx < sy ? sx : sy;
  if (g_scale > 1.8f) g_scale = 1.8f;        // don't over-blow tiny GIFs
  g_ox = (BOX - (int)(w * g_scale)) / 2;
  g_oy = (BOX - (int)(h * g_scale)) / 2;
  g_cv.fillSprite(g_bg);
  applyChrome();
  g_nextFrame = 0;
  return true;
}

static void applyChrome() {
  const int r = 22;
  // carve corners back to the panel color, then re-round the stage edge
  g_cv.fillRect(0, 0, r, r, g_panel);
  g_cv.fillArc(r, r, r - 1, r, 180, 270, g_bg);
  g_cv.fillRect(BOX - r, 0, r, r, g_panel);
  g_cv.fillArc(BOX - r - 1, r, r - 1, r, 270, 360, g_bg);
  g_cv.fillRect(0, BOX - r, r, r, g_panel);
  g_cv.fillArc(r, BOX - r - 1, r - 1, r, 90, 180, g_bg);
  g_cv.fillRect(BOX - r, BOX - r, r, r, g_panel);
  g_cv.fillArc(BOX - r - 1, BOX - r - 1, r - 1, r, 0, 90, g_bg);
  // corner interiors back to stage black
  g_cv.fillArc(r, r, 0, r - 1, 180, 270, g_bg);
  g_cv.fillArc(BOX - r - 1, r, 0, r - 1, 270, 360, g_bg);
  g_cv.fillArc(r, BOX - r - 1, 0, r - 1, 90, 180, g_bg);
  g_cv.fillArc(BOX - r - 1, BOX - r - 1, 0, r - 1, 0, 90, g_bg);
  g_cv.drawRoundRect(0, 0, BOX, BOX, r, g_border);
}

bool avatarInit(uint16_t bgColor) {
  g_panel = bgColor;
  g_bg = 0x0000;                         // pack GIFs bake a black backdrop
  g_border = (uint16_t)0x2D7B;           // ~#2A2F3A lift border
  if (!LittleFS.begin(false)) {
    Serial.println("[avatar] LittleFS mount failed — run uploadfs");
    return false;
  }
  g_cv.setPsram(true);
  g_cv.setColorDepth(16);
  if (!g_cv.createSprite(BOX, BOX)) return false;
  g_cv.fillSprite(g_bg);
  g_gif.begin(GIF_PALETTE_RGB565_BE);
  g_ready = true;
  return true;
}

bool avatarReady() { return g_ready && g_open; }

void avatarSetState(uint8_t uiState) {
  if (!g_ready || uiState == g_state) return;
  g_state = uiState;
  openState(uiState);
}

bool avatarTick() {
  if (!g_ready || !g_open) return false;
  uint32_t now = millis();
  if (now < g_nextFrame) return false;
  int delayMs = 0;
  int more = g_gif.playFrame(false, &delayMs);
  applyChrome();
  if (delayMs < 16) delayMs = 16;   // same floor as the stackchan renderer
  g_nextFrame = now + delayMs;
  if (!more) g_gif.reset();                  // loop
  return true;
}

void avatarDraw(M5Canvas& dst, int cx, int cy, int outSize) {
  if (!g_ready || !g_open) return;
  if (outSize <= 0 || outSize == BOX) {
    g_cv.pushSprite(&dst, cx - BOX / 2, cy - BOX / 2);
  } else {
    float z = (float)outSize / BOX;
    g_cv.pushRotateZoom(&dst, cx, cy, 0.0f, z, z);
  }
}

void avatarPushDirect(int cx, int cy, int outSize) {
  if (!g_ready || !g_open) return;
  if (outSize <= 0 || outSize == BOX) {
    g_cv.pushSprite(&M5.Display, cx - BOX / 2, cy - BOX / 2);
  } else {
    float z = (float)outSize / BOX;
    g_cv.pushRotateZoom(&M5.Display, cx, cy, 0.0f, z, z);
  }
}
