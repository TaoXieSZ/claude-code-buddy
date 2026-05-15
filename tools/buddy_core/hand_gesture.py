"""Hand gesture recognition for the StackChan camera-stream pipeline.

Two layers:

1. `classify_landmarks(landmarks)` — pure logic. Given 21 MediaPipe Hands
   landmarks (as (x, y) or (x, y, z) tuples), classify thumb-up / thumb-down /
   None. No mediapipe or numpy dependency — host-testable with synthetic data.

2. `classify_jpeg(payload)` — decodes a JPEG, runs MediaPipe Hands, then
   feeds the result to `classify_landmarks`. mediapipe + pillow are optional
   imports; if either is missing the function logs once and returns None.
   That keeps cc-bridge importable on machines without the heavy CV stack —
   gesture-approve simply stays dormant, manual approval still works.

Coordinate system: MediaPipe normalises to 0–1, origin TOP-LEFT, so smaller
y means HIGHER in the image. Folded finger = tip_y > MCP_y (tip curled down
toward the palm). Extended finger = tip_y < MCP_y.

Thresholds tuned on intuition and the synthetic test cases; refine on real
on-device data when available. See openspec change
2026-05-15-0003-stackchan-camera-gestures.
"""

from __future__ import annotations

import io
import logging
import os
from typing import List, Optional, Sequence, Tuple, Union

_log = logging.getLogger(__name__)

# Either (x, y) or (x, y, z) — z is silently ignored.
Landmark = Union[Tuple[float, float], Tuple[float, float, float]]


# Thumb position vs index-MCP must clear this gap (normalised y units) before
# we call it up or down. Smaller → more sensitive, more false positives.
_THUMB_GAP = 0.05


def classify_landmarks(landmarks: Optional[Sequence[Landmark]]) -> Optional[str]:
    """Pure-logic classifier. Returns 'approve' / 'deny' / None.

    Heuristic:
      - Require exactly 21 landmarks.
      - Index/middle/ring/pinky must be FOLDED (tip below their MCP in
        image y) — otherwise it's a palm/peace/point, not a thumb gesture.
      - Thumb tip well ABOVE index MCP (≥ _THUMB_GAP) → 'approve'.
      - Thumb tip well BELOW index MCP                 → 'deny'.
      - Otherwise (level / ambiguous)                  → None.
    """
    if not landmarks or len(landmarks) != 21:
        return None

    def y(idx: int) -> float:
        return landmarks[idx][1]

    # Folded: tip below MCP in image (tip_y > mcp_y).
    # Indices: (mcp, tip) for index, middle, ring, pinky.
    fingers = [(5, 8), (9, 12), (13, 16), (17, 20)]
    others_folded = all(y(tip) > y(mcp) for (mcp, tip) in fingers)
    if not others_folded:
        return None

    thumb_tip_y = y(4)
    index_mcp_y = y(5)
    if thumb_tip_y < index_mcp_y - _THUMB_GAP:
        return "approve"
    if thumb_tip_y > index_mcp_y + _THUMB_GAP:
        return "deny"
    return None


# ─── JPEG → landmarks (optional heavy deps) ───────────────────────────

# MediaPipe Tasks API model file. The legacy `mediapipe.solutions.hands`
# module was dropped in mediapipe>=0.10 builds for newer Pythons; the
# Tasks API (HandLandmarker) is the supported successor. It needs a
# `.task` model file at runtime — we cache it on first use and never
# re-download.
_HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)


def _model_cache_path() -> str:
    """`~/.cache/cc-bridge/hand_landmarker.task` (XDG-ish)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    cache_dir = os.path.join(base, "cc-bridge")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "hand_landmarker.task")


def _ensure_model() -> Optional[str]:
    """Download the HandLandmarker model if it isn't cached yet. Returns
    the local path on success, None on any failure."""
    path = _model_cache_path()
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    try:
        import urllib.request
        _log.info("Downloading hand_landmarker.task from %s", _HAND_MODEL_URL)
        urllib.request.urlretrieve(_HAND_MODEL_URL, path)
        _log.info("Cached model at %s (%d bytes)", path, os.path.getsize(path))
        return path
    except Exception as e:  # noqa: BLE001
        _log.warning("hand_landmarker.task download failed: %s", e)
        return None


# Cached on first use so we pay the model-load cost once.
_landmarker = None
_landmarker_unavailable_reason: Optional[str] = None


def _get_landmarker():
    """Lazy singleton for `mp.tasks.vision.HandLandmarker`. Returns None
    if mediapipe / pillow / numpy isn't installed OR the model file can't
    be downloaded. The first call also pulls the model (~8 MB)."""
    global _landmarker, _landmarker_unavailable_reason
    if _landmarker is not None:
        return _landmarker
    if _landmarker_unavailable_reason is not None:
        return None
    try:
        import mediapipe as mp  # noqa: F401
        from PIL import Image  # noqa: F401
        import numpy as np  # noqa: F401
    except Exception as e:  # noqa: BLE001
        _landmarker_unavailable_reason = f"vision deps missing: {e}"
        _log.warning(_landmarker_unavailable_reason)
        return None
    model_path = _ensure_model()
    if not model_path:
        _landmarker_unavailable_reason = "model file unavailable"
        return None
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
        options = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=mp_vision.RunningMode.IMAGE,  # one-shot per JPEG
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        _landmarker = mp_vision.HandLandmarker.create_from_options(options)
        _log.info("mediapipe HandLandmarker initialised")
    except Exception as e:  # noqa: BLE001
        _landmarker_unavailable_reason = f"HandLandmarker init failed: {e}"
        _log.warning(_landmarker_unavailable_reason)
        return None
    return _landmarker


def classify_jpeg(payload: bytes) -> Optional[str]:
    """Decode `payload` (JPEG bytes) → MediaPipe HandLandmarker → classify.

    Returns 'approve' / 'deny' / None. None on any failure (no hand detected,
    mediapipe missing, decode error). Errors are swallowed — never let a
    bad frame kill the daemon's per-frame callback.
    """
    if not payload:
        return None
    landmarker = _get_landmarker()
    if landmarker is None:
        return None
    try:
        import mediapipe as mp
        from PIL import Image
        import numpy as np
        img = Image.open(io.BytesIO(payload)).convert("RGB")
        rgb = np.asarray(img, dtype=np.uint8)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = landmarker.detect(mp_image)
        if not result.hand_landmarks:
            return None
        # result.hand_landmarks is a list (one entry per detected hand) of
        # lists of NormalizedLandmark objects. We requested num_hands=1.
        lm = result.hand_landmarks[0]
        pts: List[Tuple[float, float, float]] = [(p.x, p.y, p.z) for p in lm]
        return classify_landmarks(pts)
    except Exception:  # noqa: BLE001
        _log.exception("classify_jpeg: failed")
        return None
