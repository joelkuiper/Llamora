from __future__ import annotations

import hashlib
from dataclasses import dataclass
from logging import getLogger
from time import time

from aiosqlitepool import SQLiteConnectionPool
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from nacl.exceptions import CryptoError
from nacl.utils import random as random_bytes

from llamora.app.services.crypto import (
    CURRENT_SUITE,
    CryptoDescriptor,
    get_crypto_epoch,
)

logger = getLogger(__name__)

_NONCE_BYTES = 24
_MAX_NAME_LENGTH = 128


class LockboxDecryptionError(Exception):
    pass


@dataclass(slots=True)
class Lockbox:
    pool: SQLiteConnectionPool

    async def set(
        self,
        user_id: str,
        dek: bytes,
        namespace: str,
        key: str,
        value: bytes,
    ) -> None:
        self._validate_user_id(user_id)
        self._validate_name(namespace, "namespace")
        self._validate_name(key, "key")
        self._validate_dek(dek)
        scoped_namespace = self._scope_namespace(user_id, namespace)
        packed = self._encrypt(dek, user_id, namespace, key, value)
        descriptor = CryptoDescriptor(algorithm=CURRENT_SUITE, epoch=get_crypto_epoch())
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
        user_id: str,
        dek: bytes,
        namespace: str,
        key: str,
    ) -> bytes | None:
        self._validate_user_id(user_id)
        self._validate_name(namespace, "namespace")
        self._validate_name(key, "key")
        self._validate_dek(dek)
        scoped_namespace = self._scope_namespace(user_id, namespace)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT value, alg FROM lockbox WHERE namespace = ? AND key = ?",
                (scoped_namespace, key),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._decrypt(dek, user_id, namespace, key, bytes(row["value"]))

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

    def _encrypt(
        self,
        dek: bytes,
        user_id: str,
        namespace: str,
        key: str,
        plaintext: bytes,
    ) -> bytes:
        nonce = random_bytes(_NONCE_BYTES)
        aad = self._aad(user_id, namespace, key)
        ciphertext = crypto_aead_xchacha20poly1305_ietf_encrypt(
            plaintext,
            aad,
            nonce,
            dek,
        )
        return nonce + ciphertext

    def _decrypt(
        self,
        dek: bytes,
        user_id: str,
        namespace: str,
        key: str,
        packed: bytes,
    ) -> bytes:
        if len(packed) <= _NONCE_BYTES:
            raise LockboxDecryptionError("failed to decrypt lockbox value")

        nonce = packed[:_NONCE_BYTES]
        ciphertext = packed[_NONCE_BYTES:]
        aad = self._aad(user_id, namespace, key)
        try:
            return crypto_aead_xchacha20poly1305_ietf_decrypt(
                ciphertext,
                aad,
                nonce,
                dek,
            )
        except CryptoError as exc:
            logger.warning("Lockbox authentication failed")
            raise LockboxDecryptionError("failed to decrypt lockbox value") from exc

    def _aad(self, user_id: str, namespace: str, key: str) -> bytes:
        return f"{user_id}:{namespace}:{key}".encode("utf-8")

    def _scope_namespace(self, user_id: str, namespace: str) -> str:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return f"{digest}:{namespace}"

    def _validate_dek(self, dek: bytes) -> None:
        if len(dek) != 32:
            raise ValueError("invalid key material")

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
