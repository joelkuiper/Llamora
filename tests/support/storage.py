"""People and constants for storage integration tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from llamora.app.services.crypto import CryptoContext

SIZES = {"thumb": 48, "display": 128, "full": 256}
DAY = "2026-09-26"


@dataclass(slots=True)
class Person:
    id: str
    dek: bytes

    def ctx(self, *, dek: bytes | None = None, epoch: int = 1) -> CryptoContext:
        return CryptoContext(user_id=self.id, dek=dek or self.dek, epoch=epoch)


MakePerson = Callable[[str], Awaitable[Person]]
