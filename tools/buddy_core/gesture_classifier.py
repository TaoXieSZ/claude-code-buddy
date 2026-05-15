"""Hold-window debounce classifier for the StackChan gesture-approve path.

P1 of openspec/changes/2026-05-15-0003-stackchan-camera-gestures.

The frame ingest pipeline classifies each frame into a gesture string (e.g.
"approve" or "deny") or None. Raw MediaPipe output flickers — a single
misclassified frame in the middle of a thumbs-up shouldn't approve a tool.
This wrapper requires N consecutive identical non-None readings before
confirming, and then locks until the input changes so one sustained
thumbs-up fires exactly one approve (not approve-per-frame at 10 fps).

Pure logic; the actual MediaPipe call lives in cc-bridge/bridge.py.
"""

from __future__ import annotations

from typing import Optional


class GestureClassifier:
    __slots__ = ("_hold", "_streak_gesture", "_streak_count", "_fired_gesture")

    def __init__(self, *, hold_frames: int) -> None:
        if hold_frames < 1:
            raise ValueError(f"hold_frames must be >= 1, got {hold_frames}")
        self._hold = hold_frames
        self._streak_gesture: Optional[str] = None
        self._streak_count: int = 0
        # The gesture this classifier most recently confirmed. Stays set
        # until the input changes (different gesture or None), which is
        # what makes a sustained hold fire exactly once.
        self._fired_gesture: Optional[str] = None

    def classify(self, gesture: Optional[str]) -> Optional[str]:
        """Feed one per-frame gesture observation. Returns the confirmed
        gesture string on the frame it transitions from "accumulating" to
        "confirmed", and None otherwise.
        """
        if gesture is None:
            # Hand left the frame / nothing recognised. Drop the streak and
            # release the fired-lock so the next hold can refire.
            self._streak_gesture = None
            self._streak_count = 0
            self._fired_gesture = None
            return None

        if gesture != self._streak_gesture:
            # New streak. Also release the fired-lock — a different gesture
            # is a fresh decision.
            self._streak_gesture = gesture
            self._streak_count = 1
            self._fired_gesture = None
            return gesture if self._streak_count >= self._hold and self._set_fired(gesture) else None

        # Same gesture as the running streak.
        if self._fired_gesture == gesture:
            # Already confirmed this hold — stay locked until input changes.
            return None

        self._streak_count += 1
        if self._streak_count >= self._hold:
            self._fired_gesture = gesture
            return gesture
        return None

    def _set_fired(self, gesture: str) -> bool:
        # Helper used only for the N=1 first-frame path. Returns True so the
        # ternary above can fire on the same frame.
        self._fired_gesture = gesture
        return True
