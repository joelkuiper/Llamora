from __future__ import annotations

import logging

from nacl.secret import SecretBox

from .ttl_store import TTLStore

logger = logging.getLogger(__name__)

NAMESPACE = "dek_sessions"


class SessionsRepository:
    """Encrypted DEK session store.

    Thin wrapper over :class:`TTLStore` that adds SecretBox
    encryption/decryption.  Sessions use a sliding-window TTL:
    every successful load refreshes the deadline.
    """

    __slots__ = ("_store", "_box", "_ttl")

    def __init__(self, store: TTLStore, box: SecretBox, ttl: int) -> None:
        self._store = store
        self._box = box
        self._ttl = ttl

    async def store(self, sid: str, dek: bytes) -> None:
        """Encrypt and persist a DEK, overwriting any existing entry."""

        ciphertext = bytes(self._box.encrypt(dek))
        await self._store.put(NAMESPACE, sid, ciphertext, self._ttl)

    async def load(self, sid: str) -> bytes | None:
        """Load and decrypt a DEK if the session has not expired."""

        raw = await self._store.get_and_refresh(NAMESPACE, sid, self._ttl)
        if raw is None:
            return None
        try:
            return self._box.decrypt(raw)
        except Exception:
            logger.warning("Failed to decrypt session %s; removing", sid[:8])
            await self.remove(sid)
            return None

    async def remove(self, sid: str) -> None:
        """Delete a specific session."""

        await self._store.remove(NAMESPACE, sid)

    async def clear_all(self) -> int:
        """Delete all DEK sessions."""

        return await self._store.remove_namespace(NAMESPACE)
