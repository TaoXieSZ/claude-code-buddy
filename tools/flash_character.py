#!/usr/bin/env python3
"""
Flash a prepped character pack via USB (pio run -t uploadfs).
Faster than the BLE drop target when you're iterating on a character.

Usage:
  python3 tools/flash_character.py characters/calico --env cursor
  python3 tools/flash_character.py characters/clawd  --env claude

--env routes uploadfs to the matching m5stickc-plus2-<env> environment in
platformio.ini, which has upload_port pinned to a specific USB serial.
With both sticks plugged in simultaneously this prevents accidentally
flashing the wrong stick. Omit --env to use whatever pio defaults to
(the first env in platformio.ini, currently the claude stick).
"""
import argparse, json, sys, shutil, subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA    = PROJECT / "data" / "characters"
CAP     = 1_800_000
ENV_MAP = {
    "cursor": "m5stickc-plus2-cursor",
    "claude": "m5stickc-plus2-claude",
}


def flash(src: Path, env: str | None) -> None:
    if not (src / "manifest.json").exists():
        sys.exit(f"no manifest.json in {src} — run tools/prep_character.py first")
    name = json.loads((src / "manifest.json").read_text())["name"]

    total = sum(f.stat().st_size for f in src.iterdir() if f.is_file())
    if total > CAP:
        sys.exit(f"{total:,} bytes — over the {CAP:,} LittleFS cap")

    # uploadfs flashes everything under data/; the firmware only reads one
    # character at a time, so a stale sibling just wastes partition space.
    if DATA.exists():
        shutil.rmtree(DATA)
    dst = DATA / name
    shutil.copytree(src, dst)
    print(f"staged {name}: {total:,} bytes -> {dst}")

    cmd = ["pio", "run", "-t", "uploadfs"]
    if env:
        cmd.extend(["-e", ENV_MAP[env]])
        print(f"target: {ENV_MAP[env]} (pinned upload_port in platformio.ini)")
    else:
        print("target: pio default env (no --env passed)")
    subprocess.run(cmd, cwd=PROJECT, check=True)
    print(f"\nflashed. on the stick: hold A -> settings -> species -> GIF")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Stage and uploadfs a character pack.")
    p.add_argument("src", type=Path, help="path to characters/<name>/ directory")
    p.add_argument(
        "--env",
        choices=sorted(ENV_MAP.keys()),
        help="target stick env (cursor or claude); omit to use pio default",
    )
    args = p.parse_args()
    flash(args.src.resolve(), args.env)
