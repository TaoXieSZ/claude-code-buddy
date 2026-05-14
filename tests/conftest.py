"""Shared pytest fixtures.

Both bridge daemons live in modules literally named `bridge` (one in
`tools/cc-bridge/`, one in `tools/cursor-bridge/`). A naive `import bridge`
would collide, so we load each from its file path under a unique module
name via importlib. Each `bridge.py` also does its own `sys.path.insert`
off `__file__` at import time, so once loaded by path it can resolve
`buddy_core` (and cc-bridge's `dashboard`) on its own.

`buddy_core` imports `bleak` with a hard `sys.exit()` guard if missing —
it must be installed in the test env (it is pure-Python, see pyproject
[dev] extra).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_TOOLS = _REPO / "tools"

# `import buddy_core` for the daemon library tests.
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


def _load(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


import buddy_core as _buddy_core  # noqa: E402
import buddy_core.core as _buddy_core_core  # noqa: E402

_cc_bridge = _load("cc_bridge", _TOOLS / "cc-bridge" / "bridge.py")
_cursor_bridge = _load("cursor_bridge", _TOOLS / "cursor-bridge" / "bridge.py")


@pytest.fixture
def core():
    """The shared daemon library module (`tools/buddy_core/core.py`).

    `import buddy_core` is the package; `_safe_set` / `_MOD_FLAGS` and the
    rest live in the `core` submodule.
    """
    return _buddy_core_core


@pytest.fixture
def cc():
    """The cc-bridge module (`tools/cc-bridge/bridge.py`)."""
    return _cc_bridge


@pytest.fixture
def cursor():
    """The cursor-bridge module (`tools/cursor-bridge/bridge.py`)."""
    return _cursor_bridge


@pytest.fixture
def fresh_state():
    """A pristine BuddyState for each test."""
    return _buddy_core.BuddyState()
