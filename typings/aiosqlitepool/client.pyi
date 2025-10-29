from __future__ import annotations

from types import TracebackType
from typing import AsyncContextManager, Awaitable, Callable

from .protocols import Connection as Connection


class SQLiteConnectionPool:
    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: int | None = ...,
        acquisition_timeout: int | None = ...,
        idle_timeout: int | None = ...,
        operation_timeout: int | None = ...,
    ) -> None: ...

    def connection(self) -> AsyncContextManager[Connection]: ...

    async def acquire(self) -> Connection: ...

    async def release(self, conn: Connection) -> None: ...

    async def close(self) -> None: ...

    async def __aenter__(self) -> SQLiteConnectionPool: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...
