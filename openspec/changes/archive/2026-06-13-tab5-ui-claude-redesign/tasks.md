## 1. Fonts (monospace lane)

- [x] 1.1 Generate `data/fonts/mono22.vlw` with `tools/make_vlw.py --charset full` (SF Mono latin + PingFang CJK) at 22px
- [x] 1.2 Reuse the now-unused `main30.vlw` slot (transcript moved to small22): drop `main30.vlw`, net LittleFS 8.7MB → 7.1MB (well under 9.6MB)
- [x] 1.3 Repurpose the `F_MAIN30` slot → `F_MONO22` in the VLW enum/tables with a `FreeMono12pt7b` built-in fallback and `g_vlwOk` guard

## 2. Theme tokens

- [x] 2.1 Role colors via `roleRail`/`roleText` helpers reusing `th::` (USER coral, ASSISTANT blue, TOOL dim, ERROR red, SYSTEM faint)
- [x] 2.2 Centralized transcript constants (`TRANS_ROWH/TOP/IND`, `RAIL_W`)

## 3. Transcript role model

- [x] 3.1 `classifyLine()` prefix match (`you:`→USER, `buddy:`→ASSISTANT, `!fail`→ERROR, `compacted`/`session `/✓✗→SYSTEM, else TOOL)
- [x] 3.2 `DRow` extended with `role`; set in `buildRows`
- [x] 3.3 `buildRows` sets the per-role face before measuring so wrap width matches the mono lane
- [x] 3.4 Strip role prefix + leading space from displayed text

## 4. Transcript rendering

- [x] 4.1 Continuous colored role rail per message (replaces the uniform `>`), role text color
- [x] 4.2 Newest assistant line full contrast, older dimmed
- [x] 4.3 Word-aware UTF-8 wrap + hanging indent under the rail

## 5. Session tab bar

- [x] 5.1 Active card accent border + 3px coral left rail; dimmed inactive; state dot/name/word/tokens
- [x] 5.2 Tap + left/right key selection and per-session scroll preserved

## 6. Header & status chip

- [x] 6.1 Name + state chip + tool + clock on the grid with hairline divider
- [x] 6.2 Breathe an accent ring on the chip while ACTION/ATTN (header band repainted on the breathe phase)

## 7. Permission card

- [x] 7.1 Amber rail, 权限请求 eyebrow, tool/cmd hierarchy, 220×72 allow/deny targets (retained)
- [x] 7.2 Optimistic clear + keyboard Enter/y / Esc/n; verdict tagged to the originating app

## 8. Scroll affordances

- [x] 8.1 Scroll-position rail (track + thumb sized by visible/total) on the body right edge when overflowing
- [x] 8.2 "▼ 底部还有更多" hint + bottom auto-pin retained

## 9. Avatar

- [x] 9.1 Avatar reflects the selected session state (`avatarSetState(g_sess[g_sel].state)`)

## 10. Render discipline & verification

- [x] 10.1 Dirty bands preserved; body-only / chip-only updates do not full-frame push
- [x] 10.2 `pio run -e m5stack-tab5` builds clean (RAM 18.1%, Flash 23.3%)
- [x] 10.3 Flashed app + fonts (free port → upload → uploadfs → restart cursor-bridge); cursor-bridge reconnected
- [x] 10.4 Iterated on-device with the user (role rails, mono lane, tabs, avatar size/position, Agent Buddy rename, per-tab icons) — look approved
