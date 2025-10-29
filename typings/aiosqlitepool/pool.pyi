from .connection import PoolConnection as PoolConnection
from .exceptions import (
    PoolClosedError as PoolClosedError,
    PoolConnectionAcquireTimeoutError as PoolConnectionAcquireTimeoutError,
)
from .protocols import Connection as Connection
from typing import Awaitable, Callable

class Pool:
    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: int | None = 5,
        acquisition_timeout: int | None = 30,
        idle_timeout: int | None = 86400,
        operation_timeout: int | None = 10,
    ) -> None: ...
    @property
    def is_closed(self) -> bool: ...
    @property
    def size(self) -> int: ...
    async def acquire(self) -> PoolConnection: ...
    async def release(self, conn: PoolConnection): ...
    async def close(self) -> None: ...
