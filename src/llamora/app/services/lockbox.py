from __future__ import annotations

import hashlib
from dataclasses import dataclass
from logging import getLogger
from time import time

from aiosqlitepool import SQLiteConnectionPool

from llamora.app.services.crypto import CURRENT_SUITE, CryptoDescriptor, CryptoContext

logger = getLogger(__name__)

_MAX_NAME_LENGTH = 128


def _escape_like(prefix: str) -> str:
    """Escape a prefix string for use in a SQLite LIKE pattern (ESCAPE '\\')."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class LockboxDecryptionError(Exception):
    pass


@dataclass(slots=True)
class Lockbox:
    pool: SQLiteConnectionPool

    async def set(
        self,
        ctx: CryptoContext,
        namespace: str,
        key: str,
        value: bytes,
    ) -> None:
        self._validate_user_id(ctx.user_id)
        self._validate_name(namespace, "namespace")
        self._validate_name(key, "key")
        if ctx.epoch <= 0:
            logger.warning("Encryption write missing epoch metadata for lockbox.set")
            raise ValueError("missing encryption epoch metadata")
        scoped_namespace = self._scope_namespace(ctx.user_id, namespace)
        packed = ctx.encrypt_lockbox(namespace, key, value)
        descriptor = CryptoDescriptor(algorithm=CURRENT_SUITE, epoch=ctx.epoch)
        alg = descriptor.encode()
        updated_at = int(time())

        async with self.pool.connection() as conn:
            await conn.execute("BEGIN")
            await conn.execute(
                """
                INSERT INTO lockbox(namespace, key, value, alg, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, key)
                DO UPDATE SET value=excluded.value, alg=excluded.alg,
                             updated_at=excluded.updated_at
                """,
                (scoped_namespace, key, packed, alg, updated_at),
            )
            await conn.commit()

    async def get(
        self,
        ctx: CryptoContext,
        namespace: str,
        key: str,
    ) -> bytes | None:
        self._validate_user_id(ctx.user_id)
        self._validate_name(namespace, "namespace")
        self._validate_name(key, "key")
        scoped_namespace = self._scope_namespace(ctx.user_id, namespace)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT value, alg FROM lockbox WHERE namespace = ? AND key = ?",
                (scoped_namespace, key),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return ctx.decrypt_lockbox(namespace, key, bytes(row["value"]))

    async def delete(self, user_id: str, namespace: str, key: str) -> None:
        self._validate_user_id(user_id)
        self._validate_name(namespace, "namespace")
        self._validate_name(key, "key")
        scoped_namespace = self._scope_namespace(user_id, namespace)

        async with self.pool.connection() as conn:
            await conn.execute("BEGIN")
            await conn.execute(
                "DELETE FROM lockbox WHERE namespace = ? AND key = ?",
                (scoped_namespace, key),
            )
            await conn.commit()

    async def delete_namespace(self, user_id: str, namespace: str) -> None:
        self._validate_user_id(user_id)
        self._validate_name(namespace, "namespace")
        scoped_namespace = self._scope_namespace(user_id, namespace)

        async with self.pool.connection() as conn:
            await conn.execute("BEGIN")
            await conn.execute(
                "DELETE FROM lockbox WHERE namespace = ?",
                (scoped_namespace,),
            )
            await conn.commit()

    async def delete_bulk(
        self,
        user_id: str,
        ops: list[tuple[str, str | None, str | None]],
    ) -> None:
        """Atomically delete a batch of lockbox entries in a single transaction.

        Each op is ``(namespace, key_or_None, prefix_or_None)``:
        - ``key`` not None  → delete exact key
        - ``prefix == ""``  → delete entire namespace
        - ``prefix`` non-empty → delete all keys matching ``prefix*``
        """
        if not ops:
            return
        self._validate_user_id(user_id)
        async with self.pool.connection() as conn:
            await conn.execute("BEGIN")
            for namespace, key, prefix in ops:
                self._validate_name(namespace, "namespace")
                scoped = self._scope_namespace(user_id, namespace)
                if key is not None:
                    self._validate_name(key, "key")
                    await conn.execute(
                        "DELETE FROM lockbox WHERE namespace = ? AND key = ?",
                        (scoped, key),
                    )
                elif prefix is not None:
                    if prefix == "":
                        await conn.execute(
                            "DELETE FROM lockbox WHERE namespace = ?",
                            (scoped,),
                        )
                    else:
                        like_pattern = _escape_like(prefix) + "%"
                        await conn.execute(
                            "DELETE FROM lockbox WHERE namespace = ? AND key LIKE ? ESCAPE '\\'",
                            (scoped, like_pattern),
                        )
            await conn.commit()

    async def list(self, user_id: str, namespace: str) -> list[str]:
        self._validate_user_id(user_id)
        self._validate_name(namespace, "namespace")
        scoped_namespace = self._scope_namespace(user_id, namespace)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT key FROM lockbox WHERE namespace = ? ORDER BY key ASC",
                (scoped_namespace,),
            )
            rows = await cursor.fetchall()
            return [str(row[0]) for row in rows]

    def _scope_namespace(self, user_id: str, namespace: str) -> str:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return f"{digest}:{namespace}"

    def _validate_user_id(self, user_id: str) -> None:
        if not user_id:
            raise ValueError("user id must not be empty")
        if len(user_id) > _MAX_NAME_LENGTH:
            raise ValueError("user id exceeds 128 characters")

    def _validate_name(self, value: str, field_name: str) -> None:
        if not value:
            raise ValueError(f"{field_name} must not be empty")
        if len(value) > _MAX_NAME_LENGTH:
            raise ValueError(f"{field_name} exceeds 128 characters")
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{field_name} must be ASCII") from exc
