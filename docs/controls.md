# Controls

Buttons, gestures, and the PTT dictation flow for the Plus2 stick.
StackChan input UX is different (touch zones on the body) and will get
its own write-up; this page is stick-specific.

## Buttons

**Standard mode** (no BugC2, or BugC2 attached but _not_ blocking BtnB):

|                         | Normal               | Pet         | Info        | Approval    |
| ----------------------- | -------------------- | ----------- | ----------- | ----------- |
| **A** (front)           | next screen          | next screen | next screen | **approve** |
| **B** (right)           | scroll transcript    | next page   | next page   | **deny**    |
| **Hold A**              | menu                 | menu        | menu        | menu        |
| **Power** (left, short) | toggle screen off    |             |             |             |
| **Power** (left, ~6s)   | hard power off       |             |             |             |
| **Shake**               | dizzy                |             |             | —           |
| **Face-down**           | nap (energy refills) |             |             |             |

**BugC2 no-B mode** (BugC2 chassis mounted, physically covers BtnB):

Since the BugC2 base covers BtnB, the stick auto-detects this at boot and
switches button semantics so you can still drive it with A alone:

|                         | Normal / Menu       | Pet / Info  | Approval    |
| ----------------------- | ------------------- | ----------- | ----------- |
| **A** (front)           | cycle selection     | cycle pages | cycle approve↔deny |
| **Hold A**              | confirm / open menu | confirm     | confirm selection |

## PTT dictation gesture

Tap A once, then within 300ms press-and-hold A for ≥250ms. While held, a
blinking red `REC` banner shows on the top of the screen. Release to stop.
The daemon translates this to a keystroke (default: right Option) that
triggers your dictation app. Only active from the idle main screen (no
menus, no prompts).

**Picking the right PTT mode for your dictation app:**

| App                | `*_BRIDGE_PTT_MODE`   | What the daemon does                                  |
| ------------------ | --------------------- | ----------------------------------------------------- |
| Typeless           | `tap` (default)       | One down+up tap per stick gesture transition          |
| 豆包输入法 长按模式 | `hold`                | Key held while you hold A; released on release        |
| 豆包输入法 免按模式 | `double_tap`          | Double-tap on press; double-tap on release            |

The env var name is `CC_BRIDGE_PTT_MODE` for the Claude Code daemon and
`CURSOR_BRIDGE_PTT_MODE` for the Cursor daemon. Default is `tap` so the
out-of-the-box Typeless flow keeps working with no config.

To make a non-default mode survive Mac reboots, add it to the plist:

```xml
<!-- ~/Library/LaunchAgents/com.cc-bridge.plist, inside EnvironmentVariables -->
<key>CC_BRIDGE_PTT_MODE</key>
<string>hold</string>
```

Then `launchctl unload` + `launchctl load` the plist (or just reboot).
For a one-shot test without editing the plist:

```bash
launchctl setenv CC_BRIDGE_PTT_MODE hold
launchctl kickstart -k gui/$(id -u)/com.cc-bridge
```

The same `CC_BRIDGE_PTT_KEYCODE` / `CURSOR_BRIDGE_PTT_KEYCODE` (default 61
= right Option) lets you switch the relayed key if your dictation app
uses a different hotkey.

## Auto-sleep behaviour

The screen auto-powers-off after 30s of no interaction (kept on while an
approval prompt is up). After 15s of no button press / session activity, the
stick visibly nods off into idle sleep (P_SLEEP state) before the 30s
auto-off triggers. Any button press wakes it.
