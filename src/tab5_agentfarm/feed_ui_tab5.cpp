// FeedUITab5 — Agent Farm trigger feed on the Tab5 1280x720 LCD, rendered in
// the buddy dashboard's visual language (src/tab5/ui.cpp): th:: three-elevation
// dark palette, Claude coral accent, rounded lift-border cards, anti-aliased
// VLW smooth fonts, and a framed clawd GIF avatar whose mood tracks the latest
// trigger result. Transport/feed/mood behavior is unchanged — presentation only.
#include "feed_ui_tab5.h"

#include <LittleFS.h>
#include <M5Unified.h>
#include <WiFi.h>

#include "../tab5/avatar.h"  // reuse the buddy's clawd GIF avatar renderer

// ---------- theme (mirrors src/tab5/ui.cpp) ----------
#define C565(r, g, b) \
  (uint16_t)((((r)&0xF8) << 8) | (((g)&0xFC) << 3) | ((b) >> 3))
namespace th {
constexpr uint16_t BG = C565(0x0E, 0x11, 0x16);
constexpr uint16_t PANEL = C565(0x14, 0x19, 0x20);
constexpr uint16_t CARD = C565(0x1C, 0x23, 0x2E);
constexpr uint16_t CARD_HI = C565(0x24, 0x2D, 0x3A);
constexpr uint16_t ACCENT = C565(0xD9, 0x77, 0x57);
constexpr uint16_t ACCENT_DK = C565(0x8A, 0x4A, 0x36);
constexpr uint16_t TEXT = C565(0xE6, 0xED, 0xF3);
constexpr uint16_t DIM = C565(0x8B, 0x94, 0x9E);
constexpr uint16_t FAINT = C565(0x4A, 0x55, 0x62);
constexpr uint16_t IDLE = C565(0x6E, 0x76, 0x81);
constexpr uint16_t BUSY = C565(0x44, 0x93, 0xF8);
constexpr uint16_t ATTN = C565(0xD2, 0x99, 0x22);
constexpr uint16_t DONE = C565(0x3F, 0xB9, 0x50);
constexpr uint16_t ERR = C565(0xF8, 0x51, 0x49);
constexpr uint16_t INK = C565(0x10, 0x12, 0x16);  // dark text on accent pills
}  // namespace th

// ---------- layout ----------
static constexpr int W = 1280, H = 720, SB_W = 300, PAD = 24;
static constexpr int HDR_Y = 96;                  // header divider baseline
static constexpr int AV_CX = SB_W / 2, AV_CY = 372, AV_SIZE = 172;

// ---------- VLW smooth fonts (mirrors src/tab5/ui.cpp), built-in fallback ----
enum { F_MONO22, F_SMALL22, F_BOLD40, F_BOLD28, F_BOLD22, F_COUNT };
static const char* kVlwFiles[F_COUNT] = {
    "/fonts/mono22.vlw", "/fonts/small22.vlw",
    "/fonts/bold40.vlw", "/fonts/bold28.vlw", "/fonts/bold22.vlw",
};
static const lgfx::IFont* kVlwFallback[F_COUNT] = {
    &fonts::FreeMono12pt7b, &fonts::efontCN_16, &fonts::FreeSansBold24pt7b,
    &fonts::FreeSansBold18pt7b, &fonts::FreeSansBold12pt7b,
};
static lgfx::VLWfont g_vlwFont[F_COUNT];
static lgfx::PointerWrapper g_vlwWrap[F_COUNT];
static bool g_vlwOk[F_COUNT] = {};

static const lgfx::IFont* uifont(int slot) {
  return g_vlwOk[slot] ? (const lgfx::IFont*)&g_vlwFont[slot]
                       : kVlwFallback[slot];
}

static void fontsInit() {
  for (int i = 0; i < F_COUNT; i++) {
    File f = LittleFS.open(kVlwFiles[i], "r");
    if (!f) { Serial.printf("[tab5-af] font missing: %s\n", kVlwFiles[i]); continue; }
    size_t len = f.size();
    uint8_t* buf = (uint8_t*)ps_malloc(len);
    if (!buf || f.read(buf, len) != (int)len) { f.close(); free(buf); continue; }
    f.close();
    g_vlwWrap[i].set(buf, len);
    g_vlwOk[i] = g_vlwFont[i].loadFont(&g_vlwWrap[i]);
  }
}

static M5Canvas spr;

// avatar states (subset of the buddy's AState): IDLE BUSY ATTN DONE ERR
enum { AV_IDLE, AV_BUSY, AV_ATTN, AV_DONE, AV_ERR };

// ---------- result mapping ----------
static uint16_t resultColor(TrigResult r) {
  switch (r) {
    case TrigResult::Success: return th::DONE;
    case TrigResult::Error: return th::ERR;
    case TrigResult::Queued: return th::BUSY;
    case TrigResult::SkippedBusy: return th::ATTN;
    case TrigResult::SkippedPaused: return th::IDLE;
    case TrigResult::SkippedNoMatch: return th::FAINT;
    default: return th::DIM;
  }
}
static const char* resultWord(TrigResult r) {
  switch (r) {
    case TrigResult::Success: return "OK";
    case TrigResult::Error: return "ERROR";
    case TrigResult::Queued: return "QUEUED";
    case TrigResult::SkippedBusy: return "BUSY";
    case TrigResult::SkippedPaused: return "PAUSED";
    case TrigResult::SkippedNoMatch: return "SKIP";
    default: return "?";
  }
}
static const char* typeWord(TrigType t) {
  switch (t) {
    case TrigType::Slack: return "slack";
    case TrigType::Cron: return "cron";
    case TrigType::Manual: return "manual";
    case TrigType::Jira: return "jira";
    default: return "trig";
  }
}

// ---------- drawing helpers (mirror src/tab5/ui.cpp) ----------
static void card(int x, int y, int w, int h, uint16_t fill, uint16_t border,
                 int r = 14) {
  spr.fillRoundRect(x, y, w, h, r, fill);
  spr.drawRoundRect(x, y, w, h, r, border);
}

static void pill(int x, int y, int w, int h, uint16_t bg, uint16_t fg,
                 const char* txt, const lgfx::IFont* f) {
  spr.fillRoundRect(x, y, w, h, h / 2, bg);
  spr.setFont(f);
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(fg, bg);
  spr.drawString(txt, x + w / 2, y + h / 2);
}

// clawd-ish vector face fallback (used only when the GIF pack is absent).
static void avatarVec(int cx, int cy, int size, int state, uint32_t now) {
  int r = size / 2;
  spr.fillRoundRect(cx - r, cy - r, size, size, size / 4, th::ACCENT);
  spr.drawRoundRect(cx - r, cy - r, size, size, size / 4, th::ACCENT_DK);
  uint16_t ink = C565(0x21, 0x13, 0x0D);
  int ey = cy - size / 8, ex = size / 4;
  bool blink = ((now / 3100) % 7) == 3 && ((now / 130) & 1);
  if (state == AV_DONE) {
    for (int i = -10; i <= 10; i++) {
      int dy = (i * i) / 14;
      spr.drawPixel(cx - ex + i, ey + dy - 4, ink);
      spr.drawPixel(cx + ex + i, ey + dy - 4, ink);
    }
  } else if (blink) {
    spr.fillRect(cx - ex - 9, ey - 2, 18, 4, ink);
    spr.fillRect(cx + ex - 9, ey - 2, 18, 4, ink);
  } else {
    spr.fillCircle(cx - ex, ey, 9, ink);
    spr.fillCircle(cx + ex, ey, 9, ink);
  }
  int my = cy + size / 5;
  spr.fillRoundRect(cx - 12, my, 24, 8, 4, ink);
}

static String clip(const String& s, size_t n) {
  if (s.length() <= n) return s;
  return s.substring(0, n > 1 ? n - 1 : 0) + "~";
}

// ---------- FeedUITab5 ----------
void FeedUITab5::begin() {
  M5.Display.setRotation(3);  // landscape 1280x720
  M5.Display.setBrightness(kBrightOn);
  spr.setPsram(true);
  spr.setColorDepth(16);
  spr.createSprite(W, H);
  // clawd GIF avatar (mounts LittleFS); then VLW fonts off the same FS.
  if (!avatarInit(th::PANEL))
    Serial.println("[tab5-af] GIF avatar unavailable — vector fallback");
  fontsInit();  // after avatarInit (LittleFS mounted there)
  lastEventMs_ = millis();
  dirty_ = true;
}

void FeedUITab5::wake() {
  if (dimmed_) {
    M5.Display.setBrightness(kBrightOn);
    dimmed_ = false;
  }
  lastEventMs_ = millis();
}

void FeedUITab5::onNewEntries(const std::vector<TriggerLog>& fresh) {
  if (fresh.empty()) return;
  wake();
  const TrigResult r = parseResult(fresh.back().resultRaw);
  if (r == TrigResult::Error) {
    mood_ = Mood::Worried;
    M5.Speaker.tone(220, 260);
  } else if (r == TrigResult::Success) {
    mood_ = Mood::Happy;
    happyUntilMs_ = millis() + 4000;
    M5.Speaker.tone(880, 90);
  } else {
    if (mood_ == Mood::Sleep) mood_ = Mood::Idle;
    M5.Speaker.tone(523, 50);
  }
  dirty_ = true;
}

void FeedUITab5::tick(const SerialFeedClient& client) {
  const uint32_t now = millis();

  if (mood_ == Mood::Happy && now > happyUntilMs_) {
    mood_ = Mood::Idle;
    dirty_ = true;
  }
  if (now - lastEventMs_ > kSleepAfterMs && mood_ != Mood::Sleep) {
    mood_ = Mood::Sleep;
    if (!dimmed_) {
      M5.Display.setBrightness(kBrightDim);
      dimmed_ = true;
    }
    dirty_ = true;
  }
  if (client.status() != lastStatus_) {
    lastStatus_ = client.status();
    dirty_ = true;
  }

  // Drive the GIF avatar (no-ops when unchanged).
  if (mood_ == Mood::Sleep) {
    avatarSetMood(AV_MOOD_SLEEP);
  } else {
    avatarSetMood(AV_MOOD_NONE);
    uint8_t st = (mood_ == Mood::Happy)   ? AV_DONE
               : (mood_ == Mood::Worried) ? AV_ERR
                                          : AV_IDLE;
    avatarSetState(st);
  }
  bool avFrame = avatarTick();

  if (dirty_) {
    render(client);
    dirty_ = false;
  } else if (avFrame && avatarReady()) {
    avatarPushDirect(AV_CX, AV_CY, AV_SIZE);  // animate GIF, no full redraw
  }
}

void FeedUITab5::render(const SerialFeedClient& client) {
  spr.fillSprite(th::BG);
  drawPet(0, 0, SB_W, H);
  drawHeader(client);
  drawFeed(client, SB_W + PAD, HDR_Y + PAD, W - SB_W - PAD * 2,
           H - HDR_Y - PAD * 2);
  spr.pushSprite(&M5.Display, 0, 0);
}

void FeedUITab5::drawHeader(const SerialFeedClient& client) {
  const int x0 = SB_W + PAD;
  spr.setFont(uifont(F_BOLD40));
  spr.setTextDatum(TL_DATUM);
  spr.setTextColor(th::TEXT, th::BG);
  spr.drawString("Trigger Feed", x0, 26);

  const char* label;
  uint16_t col;
  switch (client.status()) {
    case AFStatus::Online: label = "LIVE"; col = th::DONE; break;
    case AFStatus::Offline: label = "OFFLINE"; col = th::ERR; break;
    case AFStatus::AuthError: label = "AUTH"; col = th::ATTN; break;
    default: label = "WIFI"; col = th::ATTN; break;
  }
  spr.setFont(uifont(F_BOLD22));
  const int chipX = x0 + spr.textWidth("Trigger Feed") + 220;
  pill(chipX, 30, 150, 48, col, th::INK, label, uifont(F_BOLD22));

  spr.drawFastHLine(x0, HDR_Y, W - x0 - PAD, th::CARD_HI);
}

void FeedUITab5::drawPet(int x, int y, int w, int h) {
  spr.fillRect(0, 0, SB_W, H, th::PANEL);
  spr.drawFastVLine(SB_W - 1, 0, H, th::CARD_HI);

  spr.setFont(uifont(F_BOLD28));
  spr.setTextDatum(ML_DATUM);
  spr.setTextColor(th::TEXT, th::PANEL);
  spr.drawString("Agent Farm", 24, 44);

  // avatar stage: a framed card behind the avatar for a deliberate seat.
  const int stage = AV_SIZE + 36;
  card(AV_CX - stage / 2, AV_CY - stage / 2, stage, stage, th::CARD,
       th::CARD_HI, 22);
  if (avatarReady()) {
    avatarDraw(spr, AV_CX, AV_CY, AV_SIZE);  // clawd GIF
  } else {
    int st = (mood_ == Mood::Happy)   ? AV_DONE
           : (mood_ == Mood::Worried) ? AV_ERR
                                      : AV_IDLE;
    avatarVec(AV_CX, AV_CY, AV_SIZE, st, millis());
  }

  const char* tag;
  uint16_t tagc;
  switch (mood_) {
    case Mood::Happy: tag = "celebrating"; tagc = th::DONE; break;
    case Mood::Worried: tag = "uh-oh"; tagc = th::ERR; break;
    case Mood::Sleep: tag = "napping"; tagc = th::FAINT; break;
    default: tag = "watching"; tagc = th::DIM; break;
  }
  pill(AV_CX - 84, AV_CY + stage / 2 + 16, 168, 44, th::CARD, tagc, tag,
       uifont(F_BOLD22));

  // footer: connection + IP
  char foot[40];
  bool up = (WiFi.status() == WL_CONNECTED);
  if (up) snprintf(foot, sizeof(foot), "%s", WiFi.localIP().toString().c_str());
  else snprintf(foot, sizeof(foot), "connecting…");
  pill(16, H - 60, SB_W - 32, 44, th::CARD, up ? th::DIM : th::FAINT, foot,
       uifont(F_SMALL22));
}

void FeedUITab5::drawFeed(const SerialFeedClient& client, int x, int y, int w,
                          int h) {
  const auto& hist = client.history();  // newest-first

  if (hist.empty()) {
    spr.setFont(uifont(F_BOLD28));
    spr.setTextDatum(TL_DATUM);
    spr.setTextColor(th::FAINT, th::BG);
    spr.drawString("waiting for triggers…", x, y + 8);
    return;
  }

  const int rowH = 76;       // card pitch
  const int ch = rowH - 12;  // card height
  const int rows = h / rowH;

  for (int i = 0; i < rows && i < (int)hist.size(); ++i) {
    const TriggerLog& log = hist[i];
    const TrigResult res = parseResult(log.resultRaw);
    const TrigType ty = parseType(log.triggerTypeRaw);
    const uint16_t rc = resultColor(res);
    const int ry = y + i * rowH;

    card(x, ry, w, ch, th::CARD, th::CARD_HI, 14);
    spr.fillRoundRect(x + 6, ry + 10, 5, ch - 20, 2, rc);  // result rail

    // trigger name (bold)
    spr.setFont(uifont(F_BOLD22));
    spr.setTextDatum(TL_DATUM);
    spr.setTextColor(th::TEXT, th::CARD);
    spr.drawString(clip(log.triggerName, 30).c_str(), x + 28, ry + 11);

    // sub-line: type · agent · time (mono "code" lane)
    char sub[96];
    snprintf(sub, sizeof(sub), "%s  ·  %s  ·  %s", typeWord(ty),
             clip(log.agentName, 24).c_str(), hhmmss(log.timestamp).c_str());
    spr.setFont(uifont(F_MONO22));
    spr.setTextColor(th::DIM, th::CARD);
    spr.drawString(sub, x + 28, ry + ch - 28);

    // result pill on the right
    const char* rw = resultWord(res);
    spr.setFont(uifont(F_SMALL22));
    int pw = spr.textWidth(rw) + 40;
    if (pw < 110) pw = 110;
    pill(x + w - pw - 16, ry + (ch - 44) / 2, pw, 44, rc, th::INK, rw,
         uifont(F_SMALL22));
  }
}
