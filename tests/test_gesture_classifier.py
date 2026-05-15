"""Tests for buddy_core.gesture_classifier.

The classifier debounces a per-frame gesture stream: only after N consecutive
identical non-None readings does it confirm. After confirming it stays
locked until the input changes (different non-None or None), so one
sustained thumbs-up triggers one approve, not approve-per-frame.

P1 of openspec/changes/2026-05-15-0003-stackchan-camera-gestures. Pure
logic — no MediaPipe import; MediaPipe lives in the cc-bridge wiring.
"""

from buddy_core.gesture_classifier import GestureClassifier


def test_does_not_fire_before_hold_window():
    gc = GestureClassifier(hold_frames=3)
    assert gc.classify("approve") is None
    assert gc.classify("approve") is None
    # Two frames isn't yet three — still None.


def test_fires_on_nth_consecutive_frame():
    gc = GestureClassifier(hold_frames=3)
    assert gc.classify("approve") is None
    assert gc.classify("approve") is None
    assert gc.classify("approve") == "approve"


def test_locked_after_firing_until_input_changes():
    """Sustained thumbs-up should approve once, not approve-per-frame."""
    gc = GestureClassifier(hold_frames=3)
    for _ in range(3):
        gc.classify("approve")
    # We just fired on the 3rd. The 4th-Nth same reading must NOT re-fire.
    assert gc.classify("approve") is None
    assert gc.classify("approve") is None


def test_different_gesture_resets_streak():
    gc = GestureClassifier(hold_frames=3)
    gc.classify("approve")
    gc.classify("approve")
    # A flicker to "deny" must reset the approve streak, not confirm anything.
    assert gc.classify("deny") is None
    # Now we need 2 more denies to confirm deny.
    assert gc.classify("deny") is None
    assert gc.classify("deny") == "deny"


def test_none_resets_streak():
    gc = GestureClassifier(hold_frames=3)
    gc.classify("approve")
    gc.classify("approve")
    assert gc.classify(None) is None
    assert gc.classify("approve") is None  # streak restarted
    assert gc.classify("approve") is None
    assert gc.classify("approve") == "approve"


def test_can_refire_after_release_and_re_hold():
    """User can approve, drop hand, then approve again — fires twice."""
    gc = GestureClassifier(hold_frames=2)
    assert gc.classify("approve") is None
    assert gc.classify("approve") == "approve"
    # Release (hand leaves frame).
    assert gc.classify(None) is None
    # Re-hold.
    assert gc.classify("approve") is None
    assert gc.classify("approve") == "approve"


def test_hold_frames_of_one_fires_immediately():
    """Edge case: N=1 means any non-None gesture fires on first sight."""
    gc = GestureClassifier(hold_frames=1)
    assert gc.classify("approve") == "approve"
    # Locked.
    assert gc.classify("approve") is None
    assert gc.classify(None) is None
    assert gc.classify("approve") == "approve"


def test_invalid_hold_frames_rejected():
    """Misconfiguration should fail loudly, not silently classify-on-first."""
    import pytest
    with pytest.raises(ValueError):
        GestureClassifier(hold_frames=0)
    with pytest.raises(ValueError):
        GestureClassifier(hold_frames=-1)
