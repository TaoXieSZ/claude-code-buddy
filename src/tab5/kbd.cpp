// USB-A HID keyboard host (M1.5) — minimal usb_host bring-up so a keyboard
// plugged into the Tab5's USB-A port can drive the dashboard. Standard
// esp-idf usb/host pattern: lib-events task + client-events task; on device
// arrival claim the first HID interface and pump its interrupt IN endpoint.
// Raw reports go to serial ("[kbd] ...") for signal verification; decoded
// keys are queued for the UI loop (ISR/task-safe single-producer ring).
//
// Power note: USB5V_EN (PI4IOE5V6408-2 P0/P3) is raised by M5GFX's Tab5
// init, so the port is already powered — no expander work needed here.
#include <Arduino.h>
#include <M5Unified.h>
#include <Wire.h>
#include "usb/usb_host.h"
#include "ui.h"

// ── Tab5 Keyboard accessory (A164) — I2C, STM32F030 @0x6D ─────────────
// Register map from m5stack/M5Unit-KEYBOARD unit_Tab5Keyboard.hpp:
//   0x02 EVENT_NUM (queue len, auto-decrement on read)
//   0x10 MODE: 0=Normal 1=HID 2=Character
//   0x30 HID_EVENT: [modifier, keycode], empty = FF FF
// Keycodes are standard USB HID usage codes → same uiKeyEvent mapping as
// the USB-A path. ExtPort1 is its OWN bus — SDA=GPIO0 SCL=GPIO1 INT=GPIO50
// (per the official KeyMatrixVisualizer example, which does exactly this
// Wire.begin(0, 1) dance on Tab5).
static constexpr uint8_t KB_ADDR = 0x6D;
static bool s_kbFound = false;

static bool kbRead(uint8_t reg, uint8_t* buf, size_t len) {
  Wire.beginTransmission(KB_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)KB_ADDR, (int)len) != (int)len) return false;
  for (size_t i = 0; i < len; i++) buf[i] = Wire.read();
  return true;
}
static bool kbWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(KB_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

static void tab5kbdInit() {
  Wire.end();
  Wire.begin(/*SDA*/0, /*SCL*/1, 400000);   // ExtPort1, upstream-verbatim
  uint8_t ver = 0;
  if (kbRead(0xFE, &ver, 1)) {
    kbWrite(0x10, 1);                        // HID mode
    s_kbFound = true;
    Serial.printf("[kbd] Tab5 keyboard on ExtPort1 (fw v%d)\n", ver);
  } else {
    Serial.println("[kbd] Tab5 keyboard not responding on ExtPort1 (SDA0/SCL1)");
  }
}

static void tab5kbdPoll() {
  if (!s_kbFound) return;
  for (int guard = 0; guard < 8; guard++) {
    uint8_t n = 0;
    if (!kbRead(0x02, &n, 1) || n == 0) return;
    uint8_t ev[2] = {0xFF, 0xFF};
    if (!kbRead(0x30, ev, 2)) return;
    if (ev[1] != 0xFF && ev[1] != 0x00) {
      Serial.printf("[kbd] key mod=%02x code=%02x\n", ev[0], ev[1]);
      feedSendKey(ev[1], ev[0]);   // relay to the Mac (dashboard is touch-driven)
    }
  }
}

static usb_host_client_handle_t s_client;
static usb_device_handle_t      s_dev;
static usb_transfer_t*          s_xfer;
static uint8_t                  s_epAddr;
static bool                     s_claimed = false;

// tiny key queue: usb client task → main loop
static volatile uint8_t  s_qKey[8];
static volatile uint8_t  s_qMod[8];
static volatile uint8_t  s_qHead = 0, s_qTail = 0;

static uint8_t s_prevKeys[6] = {0};

static void queueKey(uint8_t key, uint8_t mod) {
  uint8_t next = (s_qHead + 1) & 7;
  if (next == s_qTail) return;   // full — drop
  s_qKey[s_qHead] = key;
  s_qMod[s_qHead] = mod;
  s_qHead = next;
}

static void onReport(const uint8_t* d, int len) {
  if (len < 8) return;
  // boot keyboard report: [mods, _, k1..k6] — emit newly-pressed keys only
  for (int i = 2; i < 8; i++) {
    uint8_t k = d[i];
    if (!k) continue;
    bool wasDown = false;
    for (int j = 0; j < 6; j++) if (s_prevKeys[j] == k) wasDown = true;
    if (!wasDown) queueKey(k, d[0]);
  }
  memcpy(s_prevKeys, d + 2, 6);
}

static void xferCb(usb_transfer_t* x) {
  if (x->status == USB_TRANSFER_STATUS_COMPLETED && x->actual_num_bytes > 0) {
    char hex[64]; int n = 0;
    for (int i = 0; i < x->actual_num_bytes && i < 8; i++)
      n += snprintf(hex + n, sizeof(hex) - n, "%02x ", x->data_buffer[i]);
    Serial.printf("[kbd] report: %s\n", hex);
    onReport(x->data_buffer, x->actual_num_bytes);
  }
  if (s_claimed) usb_host_transfer_submit(x);   // keep pumping
}

static void claimHid(usb_device_handle_t dev) {
  const usb_config_desc_t* cfg;
  if (usb_host_get_active_config_descriptor(dev, &cfg) != ESP_OK) return;

  // walk the raw descriptor chain for an HID interface + its INT IN endpoint
  int off = 0;
  const usb_standard_desc_t* d = (const usb_standard_desc_t*)cfg;
  int curIntf = -1;
  while ((d = usb_parse_next_descriptor(d, cfg->wTotalLength, &off)) != nullptr) {
    if (d->bDescriptorType == USB_B_DESCRIPTOR_TYPE_INTERFACE) {
      const usb_intf_desc_t* it = (const usb_intf_desc_t*)d;
      curIntf = (it->bInterfaceClass == USB_CLASS_HID) ? it->bInterfaceNumber : -1;
      if (curIntf >= 0)
        Serial.printf("[kbd] HID interface %d (proto %d)\n",
                      it->bInterfaceNumber, it->bInterfaceProtocol);
    } else if (curIntf >= 0 && d->bDescriptorType == USB_B_DESCRIPTOR_TYPE_ENDPOINT) {
      const usb_ep_desc_t* ep = (const usb_ep_desc_t*)d;
      if ((ep->bEndpointAddress & 0x80) &&
          (ep->bmAttributes & USB_BM_ATTRIBUTES_XFERTYPE_MASK) == USB_BM_ATTRIBUTES_XFER_INT) {
        if (usb_host_interface_claim(s_client, dev, curIntf, 0) != ESP_OK) return;
        s_epAddr = ep->bEndpointAddress;
        int mps = ep->wMaxPacketSize;
        if (usb_host_transfer_alloc(mps, 0, &s_xfer) != ESP_OK) return;
        s_xfer->device_handle = dev;
        s_xfer->bEndpointAddress = s_epAddr;
        s_xfer->callback = xferCb;
        s_xfer->num_bytes = mps;
        s_claimed = true;
        usb_host_transfer_submit(s_xfer);
        Serial.printf("[kbd] claimed intf %d ep 0x%02x mps %d — keyboard live\n",
                      curIntf, s_epAddr, mps);
        return;
      }
    }
  }
  Serial.println("[kbd] no HID interface found on device");
}

static void clientCb(const usb_host_client_event_msg_t* msg, void*) {
  if (msg->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
    if (usb_host_device_open(s_client, msg->new_dev.address, &s_dev) == ESP_OK) {
      const usb_device_desc_t* dd;
      usb_host_get_device_descriptor(s_dev, &dd);
      Serial.printf("[kbd] device connected: VID %04x PID %04x\n",
                    dd->idVendor, dd->idProduct);
      claimHid(s_dev);
    }
  } else if (msg->event == USB_HOST_CLIENT_EVENT_DEV_GONE) {
    Serial.println("[kbd] device gone");
    s_claimed = false;
    if (s_dev) { usb_host_device_close(s_client, s_dev); s_dev = nullptr; }
  }
}

static void libTask(void*) {
  for (;;) {
    uint32_t flags;
    usb_host_lib_handle_events(portMAX_DELAY, &flags);
  }
}
static void clientTask(void*) {
  for (;;) usb_host_client_handle_events(s_client, portMAX_DELAY);
}

void kbdInit() {
  usb_host_config_t hc = {};
  hc.intr_flags = ESP_INTR_FLAG_LEVEL1;
  esp_err_t err = usb_host_install(&hc);
  if (err != ESP_OK) {
    Serial.printf("[kbd] usb_host_install failed: %d\n", err);
    return;
  }
  usb_host_client_config_t cc = {};
  cc.is_synchronous = false;
  cc.max_num_event_msg = 8;
  cc.async.client_event_callback = clientCb;
  cc.async.callback_arg = nullptr;
  if (usb_host_client_register(&cc, &s_client) != ESP_OK) {
    Serial.println("[kbd] client register failed");
    return;
  }
  xTaskCreate(libTask, "usbh_lib", 4096, nullptr, 2, nullptr);
  xTaskCreate(clientTask, "usbh_cli", 4096, nullptr, 2, nullptr);
  Serial.println("[kbd] usb host up — plug a keyboard into USB-A");
}

static bool s_i2cInited = false;

void kbdPoll() {
  if (!s_i2cInited) { s_i2cInited = true; tab5kbdInit(); }
  static uint32_t lastI2c = 0;
  if (millis() - lastI2c >= 30) { lastI2c = millis(); tab5kbdPoll(); }
  while (s_qTail != s_qHead) {
    uint8_t k = s_qKey[s_qTail], m = s_qMod[s_qTail];
    s_qTail = (s_qTail + 1) & 7;
    feedSendKey(k, m);   // relay to the Mac (dashboard is touch-driven)
  }
}
