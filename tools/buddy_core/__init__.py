"""
buddy_core — shared runtime for cc-bridge and cursor-bridge.

Provides BuddyState, BleWriter, permission-echo plumbing, PTT key relay,
socket protocol, and the run() entrypoint. Each bridge supplies only its
own apply_event() + config.
"""

from .core import (
    BuddyState,
    BleWriter,
    run,
)

__all__ = ["BuddyState", "BleWriter", "run"]
