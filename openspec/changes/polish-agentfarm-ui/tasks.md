# Tasks — polish-agentfarm-ui

## 1. Tab5 typography (VLW smooth fonts)

- [x] 1.1 VLW font set already present in `data/fonts/` (mono22/small22/bold22/
      bold28/bold40.vlw) — no regeneration needed; `tools/make_vlw.py` documented.
- [x] 1.2 Added `uifont()` loader to the Tab5 UI: loads `/fonts/*.vlw` from
      LittleFS into PSRAM, built-in GFX fonts as fallback (mirrors `ui.cpp`).
- [x] 1.3 Replaced built-in `fonts::*` usages in `feed_ui_tab5.cpp` with the
      `uifont()` slots (title / head / row / small / mono).

## 2. Tab5 composition + header

- [x] 2.1 Sidebar + main split aligned to dashboard geometry (PANEL sidebar,
      consistent `PAD`, divider at `HDR_Y`).
- [x] 2.2 Header band reworked: title + status chip (LIVE/OFFLINE/AUTH) +
      divider via `pill()` and `th::` colors.
- [x] 2.3 Feed rebuilt as an aligned card column with a per-card result rail.

## 3. Tab5 avatar framing

- [x] 3.1 clawd avatar seated in a centered framed stage card with balanced
      margins.
- [x] 3.2 Mood tag pill beneath the avatar; GIF fast-push path
      (`avatarPushDirect`) preserved.

## 4. Cardputer compact restyle

- [x] 4.1 Applied the `th::` palette + coral accent to `src/cardputer/feed_ui.cpp`
      (header, mini clawd squircle, compact cards, result colors).
- [x] 4.2 Compact card rows with result rail; legibility prioritized at 240×135.

## 5. Verify

- [x] 5.1 Both envs build clean: `tab5-agentfarm` and `cardputer-agentfarm` SUCCESS.
- [~] 5.2 Tab5 flashed (`uploadfs` fonts+gifs + firmware). On-device LIVE data
      check deferred: the Mac moved networks (192.168.5.96 → 172.20.7.129, new
      WiFi), so the device shows OFFLINE until `af_host` + WiFi creds are updated
      for the current location. The styled chrome (fonts/layout/avatar/header)
      renders regardless.
- [ ] 5.3 Built-in-font fallback not exercised (fonts present); code path mirrors
      the proven `ui.cpp` `uifont()` fallback.
- [ ] 5.4 Cardputer reflash pending — board not currently connected (only the
      Tab5 is plugged in). Firmware builds clean and is ready to flash.

## 6. Docs

- [x] 6.1 Updated `docs/cardputer-agentfarm.md` UI section (VLW fonts, shared
      dashboard visual language, Cardputer restyle).
