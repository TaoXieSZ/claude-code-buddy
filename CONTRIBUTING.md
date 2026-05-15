# Contributing

This is an actively maintained fork of
[`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy).
The wire protocol is documented in [REFERENCE.md](REFERENCE.md) — that's
the stable surface other implementors should target.

## What we welcome

- Bug fixes for any of the firmware targets (Plus2, StackChan/CoreS3)
- Bug fixes for the Python daemon bridges (`cc-bridge`, `cursor-bridge`)
- Corrections to `REFERENCE.md` if the protocol docs are wrong or unclear
- New character packs or GIF prep pipeline improvements
- Test coverage additions (`make test` runs both pytest + Unity suites)

## What to expect

Open an issue first for anything non-trivial — a quick description of
the problem and proposed fix helps avoid duplicate work. PRs without a
linked issue may take longer to review.

CI runs on every PR: Python tests, C++ Unity tests, and the firmware
build matrix for all five PlatformIO envs. A PR should be green before
review.

## If you want something bigger

The protocol is the stable surface — `REFERENCE.md` is the contract.
If you want to port to a different board, swap the display, or
restructure the daemons, **fork it and make it yours**. We'd rather you
build the thing you want than try to merge a large divergence here.
