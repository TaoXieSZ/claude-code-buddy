# Tasks

- [x] `BuddyState` (core.py): add `context_pct`, `model`, `limit_5h`, `limit_7d`,
      `session_ms`; emit all in `to_payload()`.
- [x] cc-bridge `apply_event`: add the `hud` event branch.
- [x] `tools/cc-bridge/statusline_hud.py`: the transparent statusline proxy.
- [x] Tests: `hud` event populates fields (test_cc_bridge); `to_payload` carries
      the new fields (test_buddy_core). `make test-py` green.
- [x] Firmware: 2-row HUD card; parse the new heartbeat fields. `pio run` +
      `pio test -e native` green; flash + on-device check.
- [x] `REFERENCE.md`: document the new heartbeat fields + the statusline proxy
      setup (settings.json `statusLine.command`).
- [x] `openspec archive 0002-hud-metrics-integration`.
