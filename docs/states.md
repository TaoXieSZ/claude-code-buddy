# The seven persona states

The state machine the firmware drives is borrowed verbatim from upstream
Claude Desktop's persona engine; everything in this fork (Plus2 + StackChan
+ BugC2 + voice + dance) just maps these states onto outputs.

| State       | Trigger                     | Feel                        |
| ----------- | --------------------------- | --------------------------- |
| `sleep`     | bridge not connected        | eyes closed, slow breathing |
| `idle`      | connected, nothing urgent   | blinking, looking around    |
| `busy`      | session actively running    | thinking, working           |
| `attention` | approval pending            | alert, **LED blinks**       |
| `celebrate` | level up (every 50 K tokens)| confetti, bouncing          |
| `dizzy`     | you shook the stick         | spiral eyes, wobbling       |
| `heart`     | approved in under 5 s       | floating hearts             |

This fork lowers the `busy` threshold from `running >= 3` to
`running >= 1`, so a single session counts as busy and the BugC2
chassis reacts. Stick semantics otherwise unchanged from upstream.

## Per-target output mapping

| State       | Plus2 sprite        | Plus2 + BugC2 ([details](bugc2.md))               | StackChan ([details](character-packs.md))   |
| ----------- | ------------------- | ------------------------------------------------- | ------------------------------------------- |
| `sleep`     | `*-sleeping.gif`    | motors off, LEDs off                              | sleeping gif, servos home                   |
| `idle`      | `*-idle.gif`        | motors off, dim cyan LEDs                         | idle gif, gentle idle wiggle (toggleable)   |
| `busy`      | `*-thinking` etc.   | 1.2 s in-place spin + chirp bleep                 | rotating busy gifs, gentle nod              |
| `attention` | `*-notification`    | 80 ms twitch every ~1.2 s, amber pulse            | attention gif, left-right look              |
| `celebrate` | `*-juggling`        | continuous gentle spin, green LEDs                | celebrate gif, dance swing                  |
| `dizzy`     | `*-conducting`      | quick alternating spin, yellow LEDs               | dizzy gif, wobble                           |
| `heart`     | `*-happy`           | pink heartbeat + small wiggle                     | heart gif (when pack supplies one)          |

## Wire shape

The daemon's heartbeat JSON has the canonical fields documented in
[`REFERENCE.md`](../REFERENCE.md); state is derived from `running`,
`waiting`, `prompt`, and the `msg` keyword (`thinking…`, `running:`,
`done:`, `approve:`, `ready`). StackChan-only extensions:

- `play`: one-shot sound clip name (lowercase, looked up under
  `/sounds/<name>.wav` on LittleFS)
- `cmd`: settings dispatch — `vol` / `bright` / `char` / `motion` /
  `idle_wiggle` (see the Dashboard section in [`README.md`](../README.md#dashboard)).
