"""Lock-free ring buffer utilities for streaming byte slices."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from multiprocessing import Array
from typing import AsyncIterator


@dataclass(slots=True)
class _Segment:
    """Metadata describing a contiguous slice inside the buffer."""

    seq: int
    start: int
    length: int


class RingBuffer:
    """A lock-free ring buffer backed by a shared memory view."""

    def __init__(self, capacity: int = 262_144) -> None:
        self._capacity = capacity
        self._buffer = Array("b", capacity, lock=False)
        self._view = memoryview(self._buffer)
        self._write_seq = 0
        self._write_pos = 0
        self._segments: deque[_Segment] = deque()
        self._readers: dict[int, int] = {}
        self._data_event = asyncio.Event()
        self._closed = False
        self._next_reader_id = 1

    def open_cursor(self) -> "RingBufferCursor":
        reader_id = self._next_reader_id
        self._next_reader_id += 1
        self._readers[reader_id] = self._write_seq
        return RingBufferCursor(self, reader_id)

    async def write(self, data: bytes | bytearray | memoryview) -> None:
        if self._closed or not data:
            return
        view = memoryview(data)
        total = len(view)
        offset = 0
        while offset < total:
            await self._wait_for_space(total - offset)
            chunk_len = min(
                total - offset,
                self._capacity - self._write_pos,
                self._available_space(),
            )
            if chunk_len <= 0:
                continue
            end = self._write_pos + chunk_len
            self._view[self._write_pos:end] = view[offset : offset + chunk_len]
            segment = _Segment(
                seq=self._write_seq,
                start=self._write_pos,
                length=chunk_len,
            )
            self._segments.append(segment)
            self._write_pos = (self._write_pos + chunk_len) % self._capacity
            self._write_seq += chunk_len
            offset += chunk_len
            self._data_event.set()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._data_event.set()

    def _available_space(self) -> int:
        if not self._readers:
            return self._capacity
        oldest = min(self._readers.values(), default=self._write_seq)
        used = self._write_seq - oldest
        return max(0, self._capacity - used)

    async def _wait_for_space(self, requested: int) -> None:
        while self._available_space() <= 0 and not self._closed:
            self._data_event.clear()
            await self._data_event.wait()
        if self._closed:
            raise asyncio.CancelledError

    async def _next_view(self, reader_id: int) -> memoryview | None:
        while True:
            seq = self._readers.get(reader_id)
            if seq is None:
                return None
            segment = self._locate_segment(seq)
            if segment is not None:
                offset = seq - segment.seq
                start = segment.start + offset
                end = start + (segment.length - offset)
                if end <= self._capacity:
                    view = self._view[start:end]
                else:
                    # This should not happen because we split writes on wrap.
                    end %= self._capacity
                    view = self._view[start:self._capacity] + self._view[0:end]
                self._readers[reader_id] = seq + len(view)
                self._prune_segments()
                return view
            if self._closed:
                return None
            self._data_event.clear()
            await self._data_event.wait()

    def _locate_segment(self, seq: int) -> _Segment | None:
        while self._segments:
            segment = self._segments[0]
            if seq < segment.seq:
                return None
            if seq < segment.seq + segment.length:
                return segment
            self._segments.popleft()
        return None

    def _prune_segments(self) -> None:
        if not self._segments:
            return
        if not self._readers:
            self._segments.clear()
            self._data_event.set()
            return
        oldest = min(self._readers.values())
        while self._segments and self._segments[0].seq + self._segments[0].length <= oldest:
            self._segments.popleft()
        self._data_event.set()

    async def _drain_reader(self, reader_id: int) -> None:
        while True:
            view = await self._next_view(reader_id)
            if view is None:
                break

    def _release_reader(self, reader_id: int) -> None:
        self._readers.pop(reader_id, None)
        self._prune_segments()


class RingBufferCursor:
    """Asynchronous iterator over slices in the parent ring buffer."""

    def __init__(self, ring: RingBuffer, reader_id: int) -> None:
        self._ring = ring
        self._reader_id = reader_id
        self._closed = False

    def __aiter__(self) -> AsyncIterator[memoryview]:
        return self

    async def __anext__(self) -> memoryview:
        if self._closed:
            raise StopAsyncIteration
        view = await self._ring._next_view(self._reader_id)
        if view is None:
            await self.aclose()
            raise StopAsyncIteration
        return view

    async def drain(self) -> None:
        if self._closed:
            return
        await self._ring._drain_reader(self._reader_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ring._release_reader(self._reader_id)

