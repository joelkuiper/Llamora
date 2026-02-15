from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import orjson

from llamora.app.services.lockbox import Lockbox, LockboxDecryptionError


@dataclass(slots=True)
class LockboxStore:
    lockbox: Lockbox

    async def get_json(
        self, user_id: str, dek: bytes, namespace: str, key: str
    ) -> Any | None:
        try:
            value = await self.lockbox.get(user_id, dek, namespace, key)
        except LockboxDecryptionError:
            return None
        if value is None:
            return None
        try:
            return orjson.loads(value)
        except orjson.JSONDecodeError:
            return None

    async def set_json(
        self,
        user_id: str,
        dek: bytes,
        namespace: str,
        key: str,
        payload: Any,
    ) -> None:
        data = orjson.dumps(payload)
        await self.lockbox.set(user_id, dek, namespace, key, data)

    async def get_text(
        self, user_id: str, dek: bytes, namespace: str, key: str
    ) -> str | None:
        payload = await self.get_json(user_id, dek, namespace, key)
        return payload if isinstance(payload, str) else None

    async def set_text(
        self, user_id: str, dek: bytes, namespace: str, key: str, value: str
    ) -> None:
        await self.set_json(user_id, dek, namespace, key, value)

    async def delete(self, user_id: str, namespace: str, key: str) -> None:
        await self.lockbox.delete(user_id, namespace, key)

    async def delete_namespace(self, user_id: str, namespace: str) -> None:
        await self.lockbox.delete_namespace(user_id, namespace)

    async def list(self, user_id: str, namespace: str) -> list[str]:
        return await self.lockbox.list(user_id, namespace)


__all__ = ["LockboxStore"]
