# Tasks — cardputer-agent-farm-feed (v1)

## 1. Project / build setup
- [x] 1.1 Add a Cardputer ADV PlatformIO env (`cardputer-agentfarm`):
      `espressif32@6.7.0`, board `esp32-s3-devkitc-1`, `M5Cardputer` lib (HEAD,
      ≥1.1.1, auto-detects ADV TCA8418). Isolated `build_src_filter +<cardputer/>`.
- [x] 1.2 Add `wifi_secrets.ini` keys (`af_host`/`af_port`/`af_secret`); host+port
      pre-filled, secret left as `PASTE_ADMIN_SECRET_HERE`; skip-worktree documented.
- [~] 1.3 LCD bring-up: code path written (`FeedUI::begin`); on-device confirm
      pending hardware.

## 2. Transport (`cardputer-agentfarm-transport`)  — src/cardputer/agentfarm_client.*
- [x] 2.1 WiFi STA connect + reconnect cadence + offline status.
- [~] 2.2 Static-IP via `af_host` (v1 default). mDNS fallback deferred (documented).
- [x] 2.3 HTTP GET `/api/logs?limit=N` with `Authorization: Bearer <secret>`.
- [x] 2.4 Stream-parse `{ items: TriggerLog[] }` with an ArduinoJson field filter
      (timestamp, trigger_name, trigger_type, agent_name, result, error).
- [x] 2.5 Dedup cursor over ISO timestamp; re-sync (no backlog alert) on log reset.
- [x] 2.6 Poll loop + backoff; 401/403 → auth-error, timeout/non-200 → offline,
      cursor preserved.

## 3. Feed + pet UX (`cardputer-trigger-pet`)  — src/cardputer/feed_ui.*
- [x] 3.1 Feed renderer: rows (type glyph + name + HH:MM:SS + result tag) 240×135.
- [x] 3.2 Aggregate mood: worried+chirp on `error`, happy+chirp on `success`,
      calm blip on `queued`/`skipped_*`.
- [x] 3.3 Sounds via `M5Cardputer.Speaker.tone` (ES8311).
- [x] 3.4 SLEEP after 60 s idle + screen dim; wake on new firing.
- [x] 3.5 Keyboard `;`/`.` scroll (navigation only; no write-back).
- [ ] 3.6 Pet sprite uses drawn primitives in v1; character-pack GIF deferred to v2.

## 4. Verify (against the real Agent Farm)  — pending hardware
- [x] 4.0 Compile clean: `pio run -e cardputer-agentfarm` → SUCCESS (RAM 15%, Flash 31%).
- [ ] 4.1 Point at running `trigger-cursor`; fire a trigger and confirm on-device.
- [ ] 4.2 Confirm error vs success vs skipped → right mood/sound.
- [ ] 4.3 Restart host; confirm re-sync without backlog replay.
- [ ] 4.4 Pull WiFi / wrong token; confirm offline + auth-error recover.

## 5. Docs
- [x] 5.1 `docs/cardputer-agentfarm.md`: flashing, `wifi_secrets.ini` shape, v1
      limits, v2 backlog, verify steps.
