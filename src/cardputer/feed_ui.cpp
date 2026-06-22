#include "feed_ui.h"

#include <M5Cardputer.h>

// ---------- theme (compact subset of src/tab5/ui.cpp th::) ----------
#define C565(r, g, b) \
  (uint16_t)((((r)&0xF8) << 8) | (((g)&0xFC) << 3) | ((b) >> 3))
namespace th {
constexpr uint16_t BG = C565(0x0E, 0x11, 0x16);
constexpr uint16_t PANEL = C565(0x16, 0x1C, 0x24);
constexpr uint16_t CARD = C565(0x1C, 0x23, 0x2E);
constexpr uint16_t CARD_HI = C565(0x2A, 0x33, 0x40);
constexpr uint16_t ACCENT = C565(0xD9, 0x77, 0x57);
constexpr uint16_t ACCENT_DK = C565(0x8A, 0x4A, 0x36);
constexpr uint16_t INK = C565(0x21, 0x13, 0x0D);
constexpr uint16_t TEXT = C565(0xE6, 0xED, 0xF3);
constexpr uint16_t DIM = C565(0x8B, 0x94, 0x9E);
constexpr uint16_t FAINT = C565(0x4A, 0x55, 0x62);
constexpr uint16_t IDLE = C565(0x6E, 0x76, 0x81);
constexpr uint16_t BUSY = C565(0x44, 0x93, 0xF8);
constexpr uint16_t ATTN = C565(0xD2, 0x99, 0x22);
constexpr uint16_t DONE = C565(0x3F, 0xB9, 0x50);
constexpr uint16_t ERR = C565(0xF8, 0x51, 0x49);
}  // namespace th

namespace {

uint16_t resultColor(TrigResult r) {
  switch (r) {
    case TrigResult::Success: return th::DONE;
    case TrigResult::Error: return th::ERR;
    case TrigResult::Queued: return th::BUSY;
    case TrigResult::SkippedBusy: return th::ATTN;
    case TrigResult::SkippedPaused: return th::IDLE;
    default: return th::FAINT;
  }
}

String clip(const String& s, size_t n) {
  if (s.length() <= n) return s;
  return s.substring(0, n > 1 ? n - 1 : 0) + "~";
}

}  // namespace

void FeedUI::begin() {
  M5Cardputer.Display.setRotation(1);
  M5Cardputer.Display.setBrightness(kBrightOn);
  M5Cardputer.Display.fillScreen(th::BG);
  M5Cardputer.Display.setTextSize(1);
  lastEventMs_ = millis();
  dirty_ = true;
}

void FeedUI::wake() {
  if (dimmed_) {
    M5Cardputer.Display.setBrightness(kBrightOn);
    dimmed_ = false;
  }
  lastEventMs_ = millis();
}

void FeedUI::onNewEntries(const std::vector<TriggerLog>& fresh) {
  if (fresh.empty()) return;
  wake();
  scrollOff_ = 0;  // jump to newest

  const TrigResult r = parseResult(fresh.back().resultRaw);
  if (r == TrigResult::Error) {
    mood_ = Mood::Worried;
    M5Cardputer.Speaker.tone(220, 220);
  } else if (r == TrigResult::Success) {
    mood_ = Mood::Happy;
    happyUntilMs_ = millis() + 4000;
    M5Cardputer.Speaker.tone(880, 70);
  } else {
    if (mood_ == Mood::Sleep) mood_ = Mood::Idle;
    M5Cardputer.Speaker.tone(523, 40);
  }
  dirty_ = true;
}

void FeedUI::scroll(int delta) {
  scrollOff_ += delta;
  if (scrollOff_ < 0) scrollOff_ = 0;
  wake();
  dirty_ = true;
}

void FeedUI::tick(const AgentFarmClient& client) {
  const uint32_t now = millis();

  if (mood_ == Mood::Happy && now > happyUntilMs_) {
    mood_ = Mood::Idle;
    dirty_ = true;
  }
  if (now - lastEventMs_ > kSleepAfterMs && mood_ != Mood::Sleep) {
    mood_ = Mood::Sleep;
    if (!dimmed_) {
      M5Cardputer.Display.setBrightness(kBrightDim);
      dimmed_ = true;
    }
    dirty_ = true;
  }
  if (client.status() != lastStatus_) {
    lastStatus_ = client.status();
    dirty_ = true;
  }

  if (dirty_) {
    render(client);
    dirty_ = false;
  }
}

void FeedUI::render(const AgentFarmClient& client) {
  M5Cardputer.Display.fillScreen(th::BG);
  drawStatusBar(client);
  const int w = M5Cardputer.Display.width();
  const int h = M5Cardputer.Display.height();
  drawPet(2, 18, 70, h - 20);
  drawFeed(client, 78, 18, w - 80, h - 18);
}

void FeedUI::drawStatusBar(const AgentFarmClient& client) {
  auto& d = M5Cardputer.Display;
  const int w = d.width();
  d.fillRect(0, 0, w, 15, th::PANEL);
  d.fillRect(0, 15, w, 1, th::CARD_HI);
  d.setTextColor(th::ACCENT, th::PANEL);
  d.setCursor(4, 4);
  d.print("Agent Farm");

  const char* label;
  uint16_t dot;
  switch (client.status()) {
    case AFStatus::Online: label = "online"; dot = th::DONE; break;
    case AFStatus::Offline: label = "offline"; dot = th::ERR; break;
    case AFStatus::AuthError: label = "auth!"; dot = th::ATTN; break;
    default: label = "wifi.."; dot = th::ATTN; break;
  }
  const int tagW = strlen(label) * 6;
  d.fillCircle(w - tagW - 12, 8, 3, dot);
  d.setTextColor(th::DIM, th::PANEL);
  d.setCursor(w - tagW - 5, 4);
  d.print(label);
}

void FeedUI::drawPet(int x, int y, int w, int h) {
  auto& d = M5Cardputer.Display;
  const int sz = 44;
  const int cx = x + w / 2;
  const int cy = y + 30;
  const int half = sz / 2;

  // mini clawd: coral squircle body, ink features per mood
  d.fillRoundRect(cx - half, cy - half, sz, sz, sz / 4, th::ACCENT);
  d.drawRoundRect(cx - half, cy - half, sz, sz, sz / 4, th::ACCENT_DK);
  const int ey = cy - 5, ex = 9;
  if (mood_ == Mood::Sleep) {
    d.fillRect(cx - ex - 4, ey, 9, 2, th::INK);
    d.fillRect(cx + ex - 5, ey, 9, 2, th::INK);
  } else {
    d.fillCircle(cx - ex, ey, 3, th::INK);
    d.fillCircle(cx + ex, ey, 3, th::INK);
  }
  if (mood_ == Mood::Happy) {
    d.drawLine(cx - 7, cy + 6, cx, cy + 10, th::INK);
    d.drawLine(cx, cy + 10, cx + 7, cy + 6, th::INK);
  } else if (mood_ == Mood::Worried) {
    d.drawLine(cx - 7, cy + 10, cx, cy + 6, th::INK);
    d.drawLine(cx, cy + 6, cx + 7, cy + 10, th::INK);
  } else {
    d.fillRoundRect(cx - 6, cy + 7, 12, 3, 1, th::INK);
  }

  const char* tag;
  uint16_t tagc;
  switch (mood_) {
    case Mood::Happy: tag = "yay"; tagc = th::DONE; break;
    case Mood::Worried: tag = "uh-oh"; tagc = th::ERR; break;
    case Mood::Sleep: tag = "zzz"; tagc = th::FAINT; break;
    default: tag = "watch"; tagc = th::DIM; break;
  }
  d.setTextColor(tagc, th::BG);
  d.setCursor(cx - (int)(strlen(tag) * 3), cy + half + 6);
  d.print(tag);
}

void FeedUI::drawFeed(const AgentFarmClient& client, int x, int y, int w,
                      int h) {
  auto& d = M5Cardputer.Display;
  const auto& hist = client.history();  // newest-first

  if (hist.empty()) {
    d.setTextColor(th::FAINT, th::BG);
    d.setCursor(x + 4, y + 6);
    d.print("waiting for");
    d.setCursor(x + 4, y + 16);
    d.print("triggers...");
    return;
  }

  const int rowH = 23;
  const int rows = h / rowH;
  const int maxChars = ((w - 10) / 6) - 1;

  for (int i = 0; i < rows; ++i) {
    const int idx = scrollOff_ + i;
    if (idx >= (int)hist.size()) break;
    const TriggerLog& log = hist[idx];
    const TrigResult res = parseResult(log.resultRaw);
    const TrigType ty = parseType(log.triggerTypeRaw);
    const uint16_t rc = resultColor(res);
    const int ry = y + i * rowH;
    const int ch = rowH - 3;

    // compact card + result rail
    d.fillRoundRect(x, ry, w, ch, 4, th::CARD);
    d.fillRect(x + 3, ry + 3, 3, ch - 6, rc);

    d.setTextColor(th::TEXT, th::CARD);
    d.setCursor(x + 10, ry + 2);
    d.print(typeGlyph(ty));
    d.print(" ");
    d.print(clip(log.triggerName, maxChars - 2).c_str());

    d.setTextColor(th::DIM, th::CARD);
    d.setCursor(x + 10, ry + 11);
    d.print(hhmmss(log.timestamp).c_str());
    d.setTextColor(rc, th::CARD);
    d.print(" ");
    d.print(resultTag(res));
  }
}
