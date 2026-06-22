# Cardputer ADV — Agent Farm desk pet (v1)

A standalone **WiFi** desk pet for the **Agent Farm** (`agent-farm`:
`trigger-cursor` + `api-server-cursor`). It polls the trigger-cursor admin API
and shows **every Trigger firing** as a live feed, reacting as a pet. Unlike the
other buddies in this repo, it uses **no BLE and no Mac daemon** — it talks HTTP
straight to Agent Farm over the LAN.

Spec: `openspec/changes/cardputer-agent-farm-feed/`.

## How it works

```
Mac on the LAN (e.g. 192.168.1.10)
  trigger-cursor admin HTTP @ 0.0.0.0:60360
    GET /api/logs   ← every trigger firing (TriggerLog), newest-first
        ▲ HTTP poll every ~4s, Authorization: Bearer <admin.secret>
Cardputer ADV (same 2.4 GHz WiFi)  →  feed + pet on the 240x135 LCD
```

- Read-only: the device **never** writes back to Agent Farm in v1.
- Dedup by ISO timestamp; first poll / host-restart re-syncs silently (no backlog
  alert). See `src/cardputer/agentfarm_client.cpp`.
- Pet mood from `TriggerLog.result`: `error` → worried + low chirp, `success` →
  happy + high chirp, `queued`/`skipped_*` → calm + soft blip. Row icon from
  `trigger_type` (`#` slack, `@` cron, `>` manual, `J` jira).
- Dozes (dim) after 60 s with no firing; wakes on the next one.
- Keyboard `;` / `.` scroll the feed history (navigation only in v1).

## Prerequisites (hard)

- **Same LAN/subnet** as the Agent Farm host. The Cardputer reaches it by LAN IP.
- Cardputer radio is **2.4 GHz only** — join a 2.4 GHz SSID. The host may be on
  5 GHz if it's the same router/subnet.
- Router must **not** isolate clients (turn off "AP isolation" / guest isolation).
- Give the host a **static IP / DHCP reservation** (the IP is baked in at build).

## Configure

Edit `wifi_secrets.ini` (kept out of git via
`git update-index --skip-worktree wifi_secrets.ini`):

```ini
[wifi_secrets]
ssid     = <your 2.4 GHz SSID>
pass     = <wifi password>
af_host  = 192.168.1.10        ; Mac running Agent Farm
af_port  = 60360               ; trigger-cursor admin.port (config.yaml)
af_secret = <admin.secret>     ; trigger-cursor admin.secret (config.yaml), bare value
```

`af_host`/`af_port` are pre-filled; paste `af_secret` from your
`agent-farm/trigger-cursor/config.yaml` (`admin.secret`).

> The admin secret is compiled into the firmware binary. Acceptable for a
> personal device on a trusted LAN; do not share the `.bin`.

## Flash

```bash
# compile only
pio run -e cardputer-agentfarm

# flash over USB-C (Cardputer ADV enumerates as usbmodem on macOS)
pio run -e cardputer-agentfarm -t upload --upload-port /dev/cu.usbmodem<NN>

# serial monitor
pio device monitor -e cardputer-agentfarm
```

No `uploadfs` needed — v1 draws the pet with primitives, no LittleFS assets.

### Flashing gotcha (Stamp-S3 USB-JTAG)

The Cardputer ADV's internal USB-Serial-JTAG bridge **drops the connection when
esptool changes baud or runs its stub** — symptom: `Stub running...` then
`A fatal error occurred: No serial data received.`. The env already pins the
working combination:

- `upload_speed = 115200` (no baud switch), and
- `upload_flags = --no-stub` (ROM loader, no stub re-enumeration).

If a future cable/host still fails, hold the Cardputer's **G0 button while
plugging USB / resetting** to force ROM download mode, then retry.

> Two ESP32-S3 boards look identical over USB (both `303A:1001` "USB JTAG/serial
> debug unit"). The Cardputer ADV here is MAC `50:78:7d:ce:7a:fc`; identify by
> MAC via `esptool.py --port <p> flash_id` rather than the unstable
> `usbmodemNNNN` number. (Flash-size/PSRAM is NOT a reliable discriminator —
> esptool's `Features` line omitted PSRAM on both boards.)

## Verify against the real Agent Farm

1. Ensure `trigger-cursor` is running on the host and reachable:
   `curl -H "Authorization: Bearer <admin.secret>" http://192.168.1.10:60360/api/logs?limit=3`
2. Fire a trigger: `POST http://<host>:60360/api/triggers/<id>/fire` (or wait for
   a `cron` trigger like `pr-helper-cron`). It should appear on-device within a
   poll cycle (~4 s).
3. Confirm `error` vs `success` vs `skipped` map to the right pet mood + chirp.
4. Restart the host → device re-syncs without replaying the backlog.
5. Pull WiFi / use a wrong secret → device shows `offline` / `auth!` and recovers.

## v1 limits (by design, not bugs)

- Polling latency of a few seconds (no push).
- `/api/logs` is in-memory (max 500, cleared on host restart) — a live feed, not
  history.
- Continuous WiFi (~132 mA) drains the 1750 mAh battery in ~10 h; assume desk
  power or raise the poll interval + rely on the idle dim.
- Times shown are the firing time (UTC) from the log; the device has no RTC sync.

## v2 backlog

Push transport (SSE/WS broadcast of `TriggerLog`) for instant + low power;
keyboard reply / voice (mic → Agent Farm ASR) / approval round-trip
(`block_action`); optionally surface `/api/status` live busy counts.

## Tab5 target (`tab5-agentfarm`)

The same feed runs on the M5Stack **Tab5** (ESP32-P4, 1280×720) — better suited
to the read-only board role thanks to the big screen. The hardware-agnostic
transport (`src/agentfarm_feed/`: WiFi poll + dedup cursor) is **shared**; only
the UI differs:

```
src/agentfarm_feed/   trigger_log.h, agentfarm_client.{h,cpp}   ← shared
src/cardputer/        feed_ui.{h,cpp}, main.cpp                 ← 240x135 UI
src/tab5_agentfarm/   feed_ui_tab5.{h,cpp}, main.cpp            ← 1280x720 UI
```

Tab5 specifics:
- WiFi rides the C6 via ESP-Hosted; the client sets the P4↔C6 SDIO pins when
  built with `-DAF_WIFI_SETPINS_TAB5` (the env defines it). Needs the one-time C6
  firmware update (`tools/tab5-c6-updater/`), same as the tab5 buddy firmware.
- P4 USB-Serial-JTAG flashes fine at the default 1.5 Mbaud (no `--no-stub`
  needed, unlike the Cardputer S3). Identify the board by chip type:
  `esptool.py --port <p> chip_id` reports `ESP32-P4` for the Tab5.
- **Dashboard-grade UI** (`polish-agentfarm-ui` change): the Tab5 UI uses the
  buddy dashboard's visual language — `th::` palette, rounded lift-border cards,
  a framed avatar stage, a header band (title + status chip + divider), and
  anti-aliased **VLW smooth fonts** loaded from LittleFS `/fonts/*.vlw`
  (generated by `tools/make_vlw.py`, pushed with `uploadfs`; built-in GFX fonts
  are the fallback when `/fonts` is absent). The Cardputer uses a compact restyle
  of the same palette.
- **clawd GIF avatar**: the sidebar reuses the buddy's GIF renderer
  (`src/tab5/avatar.cpp`, compiled into the env) playing the LittleFS pack at
  `/characters/clawd/`. Mood maps from the latest result: success→`celebrate`,
  error→`dizzy`, idle→`idle`, long-idle→`sleep`. Falls back to a vector face if
  the pack is absent. Build the pack + push it once:

```bash
tools/clawd-gif/build-pack.sh                 # downloads + processes the pack into data/
pio run -e tab5-agentfarm -t uploadfs --upload-port /dev/cu.usbmodem<NN>   # push pack
pio run -e tab5-agentfarm -t upload   --upload-port /dev/cu.usbmodem<NN>   # firmware
```

Run `uploadfs` and `upload` as **separate** commands — chaining them fails
because the device hard-resets after `uploadfs` and the firmware connect can't
reattach to the re-enumerated USB port.

## Repo note

The tracked `wifi_secrets.ini` placeholder should gain `af_host`/`af_port`/
`af_secret` placeholder keys so a clean clone builds; real values stay local via
skip-worktree.
