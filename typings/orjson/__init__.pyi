from typing import Any, Protocol

class SupportsWrite(Protocol):
    def write(self, __s: str) -> object: ...

class JSONDecodeError(ValueError): ...
class JSONEncodeError(Exception): ...

def dumps(
    obj: Any,
    *,
    default: Any | None = ...,
    option: int | None = ...,
) -> bytes: ...
def loads(__obj: bytes | bytearray | memoryview | str) -> Any: ...

__all__ = [
    "JSONDecodeError",
    "JSONEncodeError",
    "dumps",
    "loads",
]
