"""Tests for buddy_core.frame_server.

Spins the server up on an ephemeral localhost port, drives it with a real
asyncio client socket, and asserts that complete JPEG payloads are
dispatched to the callback. The FrameDeframer is exercised indirectly —
this layer's job is the asyncio glue (accept, recv loop, dispatch).

Each test wraps its async logic in `asyncio.run()` so the project's pytest
setup doesn't grow a pytest-asyncio dependency.
"""

import asyncio
import struct

from buddy_core.frame_server import FrameServer


def _frame(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


async def _wait_for(predicate, *, timeout: float = 0.5, interval: float = 0.01):
    """Poll `predicate` until truthy or timeout (s)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)


def _drive(send_steps):
    """Run a server, run `send_steps(server)` against it, return collected frames.

    `send_steps` is an async callable that takes the bound server and does
    its own client-side I/O.
    """
    received: list[bytes] = []

    async def main():
        server = FrameServer(host="127.0.0.1", port=0, on_frame=received.append)
        await server.start()
        serve_task = asyncio.create_task(server.serve_forever())
        try:
            await send_steps(server, received)
        finally:
            serve_task.cancel()
            await asyncio.gather(serve_task, return_exceptions=True)
            await server.stop()

    asyncio.run(main())
    return received


def test_single_frame_dispatches_callback():
    async def steps(server, received):
        _, w = await asyncio.open_connection("127.0.0.1", server.port)
        w.write(_frame(b"hello"))
        await w.drain()
        await _wait_for(lambda: received == [b"hello"])
        w.close()
        await w.wait_closed()

    assert _drive(steps) == [b"hello"]


def test_multiple_frames_in_one_write():
    async def steps(server, received):
        _, w = await asyncio.open_connection("127.0.0.1", server.port)
        w.write(_frame(b"AAA") + _frame(b"BBBB") + _frame(b"C"))
        await w.drain()
        await _wait_for(lambda: len(received) >= 3)
        w.close()
        await w.wait_closed()

    assert _drive(steps) == [b"AAA", b"BBBB", b"C"]


def test_reconnect_after_disconnect():
    """Stackchan opens/closes the socket once per prompt window. The server
    must accept the next connection after a prior one closed."""

    async def steps(server, received):
        _, w1 = await asyncio.open_connection("127.0.0.1", server.port)
        w1.write(_frame(b"first"))
        await w1.drain()
        await _wait_for(lambda: received == [b"first"])
        w1.close()
        await w1.wait_closed()

        _, w2 = await asyncio.open_connection("127.0.0.1", server.port)
        w2.write(_frame(b"second"))
        await w2.drain()
        await _wait_for(lambda: received == [b"first", b"second"])
        w2.close()
        await w2.wait_closed()

    assert _drive(steps) == [b"first", b"second"]


def test_split_frame_across_writes():
    """Worst-case TCP fragmentation — bytes drip in across recv calls."""

    async def steps(server, received):
        raw = _frame(b"split me")
        _, w = await asyncio.open_connection("127.0.0.1", server.port)
        for i in range(len(raw)):
            w.write(raw[i:i + 1])
            await w.drain()
            await asyncio.sleep(0.001)
        await _wait_for(lambda: received == [b"split me"])
        w.close()
        await w.wait_closed()

    assert _drive(steps) == [b"split me"]
