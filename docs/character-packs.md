# Character packs

How the GIF animation system works: the `manifest.json` shape, file
naming, the sizing pipeline, and the upstream art credits.

## Built-in packs

| Pack       | Source                                                                                                | Target     |
| ---------- | ----------------------------------------------------------------------------------------------------- | ---------- |
| `clawd`    | [`rullerzhou-afk/clawd-on-desk`](https://github.com/rullerzhou-afk/clawd-on-desk) — pixel-art crab    | Plus2 (default), CoreS3 |
| `calico`   | same — three-tone cat. **Broken on CoreS3** (green-channel rendering bug, fix pending)                | Plus2      |
| `bufo`     | upstream `anthropics/claude-desktop-buddy` — frog mascot                                              | Plus2 (legacy) |
| `cloudling`| [`rullerzhou-afk/clawd-on-desk`](https://github.com/rullerzhou-afk/clawd-on-desk) — cloud sprite      | CoreS3 (default) |

Huge thanks to [@rullerzhou-afk](https://github.com/rullerzhou-afk)
for the art. If you like any of these, go star their repo — there are
many more poses (`-juggling`, `-conducting`, `-sweeping`, `-carrying`,
`-mini-*` variants) that can be wired up with a manifest edit.

## State → GIF mapping (clawd example)

| Our state   | Clawd GIF                                              | Preview |
| ----------- | ------------------------------------------------------ | --- |
| `sleep`     | `clawd-sleeping.gif`                                   | <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-sleeping.gif" width="96"> |
| `idle`      | `clawd-idle.gif`                                       | <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-idle.gif" width="96"> |
| `busy`      | `clawd-thinking` / `typing` / `building` (rotates)     | <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-thinking.gif" width="80"> <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-typing.gif" width="80"> <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-building.gif" width="80"> |
| `attention` | `clawd-notification.gif`                               | <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-notification.gif" width="96"> |
| `celebrate` | `clawd-juggling.gif`                                   | <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-juggling.gif" width="96"> |
| `dizzy`     | `clawd-conducting.gif`                                 | <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-conducting.gif" width="96"> |
| `heart`     | `clawd-happy.gif`                                      | <img src="https://raw.githubusercontent.com/rullerzhou-afk/clawd-on-desk/main/assets/gif/clawd-happy.gif" width="96"> |

The cloudling pack on CoreS3 follows the same schema with `cloudling-*`
files mapped to the same state names. CHAR_HEART is omitted (no
matching source frame).

## manifest.json

A character pack is a folder with a `manifest.json` and source GIFs at
any size:

```json
{
  "name": "clawd",
  "colors": { "body": "#D97757", "bg": "#000000", ... },
  "states": {
    "sleep": "clawd-sleeping.gif",
    "idle":  "clawd-idle.gif",
    "busy":  ["clawd-thinking.gif", "clawd-typing.gif", "clawd-building.gif"]
  }
}
```

State values can be a single filename or an array; arrays rotate so the
home screen doesn't loop one clip forever. Colors are read by the
firmware for status-bar text + background fills.

## Adding your own pack

Drag the folder onto the Hardware Buddy window (streams over BLE), or
for fast iteration:

```bash
python3 tools/prep_character.py /path/to/source-gifs
python3 tools/flash_character.py characters/<name>
```

`prep_character.py` resizes to **120 px wide** (was 96 upstream — the
larger size makes idle/sleep poses readable on Plus2's 135×240 screen).
Each state is cropped to its **own bbox**, not a global bbox — small
poses (idle/sleep) no longer get padded out to match the widest pose
(juggling/conducting).

The whole folder must fit under 1.8 MB on Plus2 (LittleFS partition
budget). `gifsicle --lossy=80 -O3 --colors 64` typically cuts 40–60% if
you bust the cap.

## StackChan-specific notes

On CoreS3 the firmware float-scales each GIF to a uniform 170 px output
height (set in `src/stackchan/character_chan.cpp:TARGET_H`), so source
GIFs of any aspect ratio render at a consistent visual size. The
landscape rotation is fixed (`setRotation(1)`, 320×240).

Hot-swap at runtime: the localhost dashboard (`http://127.0.0.1:18765/`)
has a character-pack dropdown that calls `characterReload()`, which
re-runs init with the new pack and re-opens the current state's GIF.
The new pack must already be on LittleFS — drop into `data/characters/`
and `pio run -t uploadfs` before switching.
