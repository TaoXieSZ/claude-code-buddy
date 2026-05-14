# Development workflow

This project uses **spec-driven** + **test-driven** development. Behaviour is
specified in `openspec/specs/`, locked down by tests, and changes flow through
[OpenSpec](https://github.com/Fission-AI/OpenSpec).

## One-time setup

```bash
# Python daemon tests (bleak is pure-Python; pyobjc-Quartz is macOS-only)
pip install -e ".[dev]"

# OpenSpec CLI is used via npx — no install needed, but pin awareness:
# this repo was set up with @fission-ai/openspec (core profile).
```

## Reloading the daemons after a Python change

The bridge daemons run under launchd and **do not hot-reload** — a long-lived
process keeps the old `bridge.py` / `buddy_core` in memory. After editing any
daemon Python, restart it or your change silently won't take effect:

```bash
launchctl kickstart -k gui/$(id -u)/com.cc-bridge      # cc-bridge
# cursor-bridge is the user's own side — don't auto-restart it
```

The statusline proxy (`statusline_hud.py`) is the exception: Claude Code spawns
it fresh on every render, so edits there take effect on the next statusline tick
— no restart needed.

## Running tests

```bash
make test        # both suites
make test-py     # pytest — Python daemons (tests/)
make test-cpp    # pio test -e native — pure C++ logic (Unity)
```

CI (`.github/workflows/ci.yml`) runs both suites plus the firmware build matrix
on every PR.

## What's tested

- **Python** (`tests/`) — the pure logic in the bridge daemons: `apply_event()`
  for both bridges, `BuddyState`, `_safe_set`, `_MOD_FLAGS`. The two `bridge.py`
  files share a module name, so `conftest.py` loads each by path under a unique
  name.
- **C++** (`test/`) — pure logic extracted into M5Unified-free headers (start:
  `src/stackchan/color_util.h`). Hardware-coupled code (BLE, LCD, GIF decode) is
  not unit-tested; it's covered by `pio run` compile checks + on-device QA.

## The spec → test → code loop

```text
/opsx:propose <name>   →  openspec/changes/<name>/ with proposal.md, design.md,
                          tasks.md, and a delta spec (ADDED/MODIFIED/REMOVED
                          requirements, each with GIVEN/WHEN/THEN scenarios)

translate each scenario →  a failing test (pytest or Unity)
                           run the suite — new tests are red

/opsx:apply            →  implement until `make test` is green

/opsx:archive <name>   →  the delta merges into openspec/specs/<domain>/spec.md;
                          the change folder moves to openspec/changes/archive/
```

The `GIVEN/WHEN/THEN` scenarios in a delta spec map 1:1 onto test cases — that's
the join between the spec and the tests.

**Worked example:** `openspec/changes/archive/2026-05-14-0001-heartbeat-counter-lifecycle/`
is the first change run through this loop end to end — a bug fix
(`waiting` counter never reset) proposed, spec'd, tested, fixed, and archived.

## When a change needs an OpenSpec entry

Behaviour changes to `apply_event` / `BuddyState` / the firmware state machine /
the wire protocol go through an OpenSpec change — not a bare edit. Pure
refactors, build config, docs, and asset changes don't.

## Validating specs

```bash
npx @fission-ai/openspec validate --specs        # all source-of-truth specs
npx @fission-ai/openspec validate <change-name>  # a proposed change
```
