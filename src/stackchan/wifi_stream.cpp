// WiFi + TCP frame-out implementation.
//
// Build-time -D defines provided by wifi_secrets.ini (see platformio.ini
// [platformio] extra_configs). If you left the placeholders in
// wifi_secrets.ini, wifiStreamStart() bails early with a log line —
// the rest of the firmware (manual approval, etc.) stays usable.

#include "wifi_stream.h"

#include <M5Unified.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <string.h>

#include "frame_framing.h"

#ifndef STACKCHAN_WIFI_SSID
#define STACKCHAN_WIFI_SSID "REPLACE_ME_SSID"
#endif
#ifndef STACKCHAN_WIFI_PASS
#define STACKCHAN_WIFI_PASS "REPLACE_ME_PASS"
#endif
#ifndef STACKCHAN_DAEMON_HOST
#define STACKCHAN_DAEMON_HOST "192.168.1.10"
#endif
#ifndef STACKCHAN_DAEMON_PORT
#define STACKCHAN_DAEMON_PORT 8770
#endif

static WiFiClient s_client;
static bool s_connected = false;
// WiFi-associated; tracked separately from the socket. Stays true across
// prompt windows so we don't re-associate (~1s) on every prompt.
static bool s_wifi_up = false;

static bool placeholdersUnset() {
    // If the user never edited wifi_secrets.ini, refuse to attempt connect.
    // The literal here matches the placeholder in the tracked file; both must
    // change together if you rename them.
    return strcmp(STACKCHAN_WIFI_SSID, "REPLACE_ME_SSID") == 0;
}

static bool ensureWifi() {
    if (s_wifi_up && WiFi.status() == WL_CONNECTED) return true;

    M5_LOGI("wifiStream: associating with %s...", STACKCHAN_WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(STACKCHAN_WIFI_SSID, STACKCHAN_WIFI_PASS);

    // ~6s budget. Don't block forever — manual approval still works without
    // WiFi, so a hung associate would be worse than giving up.
    const uint32_t deadline_ms = millis() + 6000;
    while (WiFi.status() != WL_CONNECTED && millis() < deadline_ms) {
        delay(100);
    }
    s_wifi_up = (WiFi.status() == WL_CONNECTED);
    if (!s_wifi_up) {
        M5_LOGW("wifiStream: WiFi associate timeout");
        return false;
    }
    M5_LOGI("wifiStream: WiFi up, ip=%s", WiFi.localIP().toString().c_str());
    return true;
}

bool wifiStreamStart() {
    if (s_connected && s_client.connected()) return true;

    if (placeholdersUnset()) {
        M5_LOGW("wifiStream: wifi_secrets.ini placeholders unset, skipping");
        return false;
    }
    if (!ensureWifi()) return false;

    // Bounded retry on TCP connect — the daemon may not be running yet.
    for (int attempt = 0; attempt < 3; ++attempt) {
        if (s_client.connect(STACKCHAN_DAEMON_HOST, STACKCHAN_DAEMON_PORT, 1500)) {
            s_connected = true;
            M5_LOGI("wifiStream: connected to %s:%d",
                    STACKCHAN_DAEMON_HOST, STACKCHAN_DAEMON_PORT);
            return true;
        }
        delay(200);
    }
    M5_LOGW("wifiStream: TCP connect to %s:%d failed after retries",
            STACKCHAN_DAEMON_HOST, STACKCHAN_DAEMON_PORT);
    return false;
}

bool wifiStreamSendFrame(const uint8_t* jpg, size_t len) {
    if (!s_connected || !s_client.connected() || !jpg || len == 0) {
        s_connected = false;  // socket may have gone dead
        return false;
    }
    uint8_t header[4];
    writeFrameLengthLE(static_cast<uint32_t>(len), header);

    // WiFiClient::write returns bytes written; partial writes for big
    // frames are possible. QVGA RGB565 → JPEG @ q80 is ~6-12 KB which
    // fits the TCP buffer in one shot in practice, but loop defensively.
    if (s_client.write(header, 4) != 4) {
        M5_LOGW("wifiStreamSendFrame: header write short");
        s_connected = false;
        return false;
    }
    size_t sent = 0;
    while (sent < len) {
        size_t n = s_client.write(jpg + sent, len - sent);
        if (n == 0) {
            M5_LOGW("wifiStreamSendFrame: payload write stalled at %u/%u",
                    (unsigned)sent, (unsigned)len);
            s_connected = false;
            return false;
        }
        sent += n;
    }
    return true;
}

void wifiStreamStop() {
    if (!s_connected) return;
    s_client.stop();
    s_connected = false;
    M5_LOGI("wifiStream: socket closed (WiFi stays up)");
}

bool wifiStreamIsConnected() {
    return s_connected && s_client.connected();
}

bool wifiStreamCredsAvailable() {
    // Inverse of the internal placeholdersUnset() guard — exposed so
    // main.cpp's arm-decider can short-circuit the cameraStart/Stop bounce
    // when wifi_secrets.ini hasn't been edited from its tracked default.
    return !placeholdersUnset();
}
