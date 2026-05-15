"""Asyncio TCP frame-ingest server for the StackChan camera stream.

P0 of openspec/changes/2026-05-15-0003-stackchan-camera-gestures.

The StackChan firmware (src/stackchan/wifi_stream.cpp) opens one TCP
connection per permission-prompt window and sends each captured JPEG
prefixed with a 4-byte little-endian length. This server accepts that
connection, feeds incoming bytes to a FrameDeframer, and dispatches each
complete JPEG payload to an injected callback.

Why a callback rather than an async iterator: the natural consumer is a
synchronous-ish MediaPipe classifier that the caller plugs in; this keeps
the server unaware of MediaPipe (kept optional in P1 per the daemon-event-
mapping spec) and host-testable without it. The callback runs on the
server's event loop — keep it cheap; offload heavy work via
loop.run_in_executor if needed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from .frame_deframer import FrameDeframer

_log = logging.getLogger(__name__)

FrameCallback = Callable[[bytes], None]


class FrameServer:
    """One-StackChan TCP frame-ingest server.

    The spec says only one StackChan connects, but the implementation accepts
    multiple concurrent connections defensively (each gets its own deframer)
    so a stale connection from a crashed firmware doesn't lock out reconnects.
    """

    def __init__(self, *, host: str, port: int, on_frame: FrameCallback) -> None:
        self.host = host
        self.port = port  # 0 → ephemeral; resolved to the bound port in start()
        self.on_frame = on_frame
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        """Bind the listener. After this returns, `self.port` is the bound port."""
        self._server = await asyncio.start_server(
            self._handle, self.host, self.port
        )
        socks = self._server.sockets or ()
        if socks:
            self.port = socks[0].getsockname()[1]
        _log.info("FrameServer listening on %s:%d", self.host, self.port)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("FrameServer.start() must be called before serve_forever()")
        await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:  # noqa: BLE001
            # asyncio.Server.wait_closed can raise on already-cancelled loops
            # during test teardown; swallow rather than mask the real error.
            pass
        self._server = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        _log.info("FrameServer: connect from %s", peer)
        deframer = FrameDeframer()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break  # EOF — StackChan closed the socket
                for payload in deframer.feed(chunk):
                    try:
                        self.on_frame(payload)
                    except Exception:  # noqa: BLE001
                        # A bad callback must not kill the recv loop or
                        # leave the StackChan reconnecting forever.
                        _log.exception("FrameServer: on_frame callback raised")
        except (ConnectionResetError, asyncio.IncompleteReadError):
            # Expected when the firmware drops the link on prompt-clear.
            pass
        finally:
            _log.info("FrameServer: disconnect from %s", peer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
