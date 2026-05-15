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

# Cached on first use so we pay the import / model-load cost once.
_hands_solution = None
_hands_unavailable_reason: Optional[str] = None


def _get_hands_solution():
    """Lazy singleton for `mediapipe.solutions.hands.Hands`. Returns None
    if mediapipe or pillow isn't installed; logs the reason once."""
    global _hands_solution, _hands_unavailable_reason
    if _hands_solution is not None:
        return _hands_solution
    if _hands_unavailable_reason is not None:
        return None
    try:
        import mediapipe as mp  # noqa: F401
    except Exception as e:  # noqa: BLE001
        _hands_unavailable_reason = f"mediapipe import failed: {e}"
        _log.warning(_hands_unavailable_reason)
        return None
    try:
        import PIL  # noqa: F401
    except Exception as e:  # noqa: BLE001
        _hands_unavailable_reason = f"pillow import failed: {e}"
        _log.warning(_hands_unavailable_reason)
        return None
    try:
        import mediapipe as mp
        _hands_solution = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        _log.info("mediapipe Hands initialised")
    except Exception as e:  # noqa: BLE001
        _hands_unavailable_reason = f"Hands init failed: {e}"
        _log.warning(_hands_unavailable_reason)
        return None
    return _hands_solution


def classify_jpeg(payload: bytes) -> Optional[str]:
    """Decode `payload` (JPEG bytes) → MediaPipe Hands → classify.

    Returns 'approve' / 'deny' / None. None on any failure (no hand detected,
    mediapipe missing, decode error). Errors are swallowed — never let a
    bad frame kill the daemon's per-frame callback.
    """
    if not payload:
        return None
    hands = _get_hands_solution()
    if hands is None:
        return None
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(io.BytesIO(payload)).convert("RGB")
        rgb = np.asarray(img, dtype=np.uint8)
        result = hands.process(rgb)
        if not result.multi_hand_landmarks:
            return None
        lm = result.multi_hand_landmarks[0].landmark
        # Convert MediaPipe NormalizedLandmark objects to plain tuples.
        pts: List[Tuple[float, float, float]] = [(p.x, p.y, p.z) for p in lm]
        return classify_landmarks(pts)
    except Exception:  # noqa: BLE001
        _log.exception("classify_jpeg: failed")
        return None
