# Convenience targets for the two physical sticks on this dev bench.
# upload_port is pinned per env in platformio.ini, so these go to the
# right stick automatically.
#
# After ANY firmware reflash, also run the matching `-fs` target if the
# character pack changed since the last fs flash. `pio run -t upload`
# does NOT touch LittleFS, so it's easy to silently end up running new
# firmware on top of stale GIFs (this happened once during the cursor
# onboarding session and surfaced as the calico green-bg pixel rot bug).
# When in doubt, use the un-suffixed `flash-cursor` / `flash-claude`
# which does both in one shot.

PIO ?= pio

.PHONY: flash-cursor flash-cursor-fw flash-cursor-fs \
        flash-claude flash-claude-fw flash-claude-fs \
        monitor-cursor monitor-claude scan-sticks help

help:
	@echo "Two-stick flash + monitor convenience targets:"
	@echo "  make flash-cursor        firmware + LittleFS to the cursor stick"
	@echo "  make flash-cursor-fw     firmware only"
	@echo "  make flash-cursor-fs     LittleFS only"
	@echo "  make flash-claude        firmware + LittleFS to the claude stick"
	@echo "  make flash-claude-fw     firmware only"
	@echo "  make flash-claude-fs     LittleFS only"
	@echo "  make monitor-cursor      pio device monitor on cursor stick"
	@echo "  make monitor-claude      pio device monitor on claude stick"
	@echo "  make scan-sticks         list attached USB serial devices"
	@echo ""
	@echo "Character pack staging is separate — use:"
	@echo "  python3 tools/flash_character.py characters/calico --env cursor"

flash-cursor:    ; $(PIO) run -e m5stickc-plus2-cursor -t upload -t uploadfs
flash-cursor-fw: ; $(PIO) run -e m5stickc-plus2-cursor -t upload
flash-cursor-fs: ; $(PIO) run -e m5stickc-plus2-cursor -t uploadfs

flash-claude:    ; $(PIO) run -e m5stickc-plus2-claude -t upload -t uploadfs
flash-claude-fw: ; $(PIO) run -e m5stickc-plus2-claude -t upload
flash-claude-fs: ; $(PIO) run -e m5stickc-plus2-claude -t uploadfs

monitor-cursor:  ; $(PIO) device monitor -e m5stickc-plus2-cursor
monitor-claude:  ; $(PIO) device monitor -e m5stickc-plus2-claude

# List FTDI/CP210x serials currently attached. Cross-check against the
# upload_port pins in platformio.ini if the wrong stick is being targeted.
scan-sticks:
	@echo "USB serial devices:"; ls /dev/cu.usbserial* 2>/dev/null || echo "  (none)"
