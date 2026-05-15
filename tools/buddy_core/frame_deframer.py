"""Pure-logic deframer for the StackChan camera-stream wire format.

Frames arrive over TCP as `<uint32 LE length><JPEG bytes>`. TCP `recv` does
not preserve message boundaries — headers and payloads can split across
recv calls, and several frames can also arrive in one recv. This class
turns an arbitrary byte stream into a sequence of complete JPEG payloads,
keeping any incomplete tail for the next feed.

The matching firmware-side header builder is in
`src/stackchan/frame_framing.h::writeFrameLengthLE`. See the openspec
change `2026-05-15-0003-stackchan-camera-gestures` for the wire contract.

Pure logic only — no socket / no asyncio. The actual asyncio TCP server
wraps an instance of this class and feeds it whatever `reader.read()`
returns. Keeps the parsing host-testable without a network.
"""

from __future__ import annotations

import struct
from typing import List

_HEADER = struct.Struct("<I")
_HEADER_LEN = _HEADER.size  # 4


class FrameDeframer:
    """Length-prefix framing accumulator. Not thread-safe; one per stream."""

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> List[bytes]:
        """Append `chunk` to the internal buffer and return any complete
        JPEG payloads now available, in arrival order. Leftover bytes
        (partial header or partial payload) stay buffered for the next
        feed."""
        if chunk:
            self._buf.extend(chunk)
        out: List[bytes] = []
        while True:
            if len(self._buf) < _HEADER_LEN:
                break
            (length,) = _HEADER.unpack_from(self._buf, 0)
            total = _HEADER_LEN + length
            if len(self._buf) < total:
                break
            # bytes() to detach from the bytearray slice — callers store these.
            out.append(bytes(self._buf[_HEADER_LEN:total]))
            del self._buf[:total]
        return out
