from __future__ import annotations

import asyncio

import numpy as np
from aiosqlitepool import SQLiteConnectionPool

from .base import BaseRepository
from llamora.app.services.crypto import EncryptionContext


class VectorsRepository(BaseRepository):
    """Persistence helpers for encrypted vector embeddings."""

    def __init__(
        self, pool: SQLiteConnectionPool, encrypt_vector, decrypt_vector
    ) -> None:
        super().__init__(pool)
        self._encrypt_vector = encrypt_vector
        self._decrypt_vector = decrypt_vector

    async def store_vector(
        self,
        vector_id: str,
        entry_id: str,
        vec: np.ndarray,
        ctx: EncryptionContext,
        dtype: str = "float32",
    ) -> None:
        dim, nonce, ct, alg = await asyncio.to_thread(
            self._prepare_encrypted_vector,
            vec,
            ctx,
            entry_id,
            vector_id,
            dtype,
        )
        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.execute,
                """
                    INSERT OR REPLACE INTO vectors (
                        id, entry_id, user_id, chunk_index, dim, nonce, ciphertext, alg, dtype
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE EXISTS (
                        SELECT 1
                        FROM entries
                        WHERE id = ? AND user_id = ?
                    )
                    """,
                (
                    vector_id,
                    entry_id,
                    ctx.user_id,
                    0,
                    dim,
                    nonce,
                    ct,
                    alg,
                    dtype,
                    entry_id,
                    ctx.user_id,
                ),
            )

    async def store_vectors_batch(
        self,
        vectors: list[tuple[str, str, int, np.ndarray]],
        ctx: EncryptionContext,
        dtype: str = "float32",
    ) -> None:
        if not vectors:
            return

        records = await asyncio.to_thread(
            self._prepare_batch_records,
            vectors,
            ctx,
            dtype,
        )

        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.executemany,
                """
                    INSERT OR REPLACE INTO vectors (
                        id, entry_id, user_id, chunk_index, dim, nonce, ciphertext, alg, dtype
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE EXISTS (
                        SELECT 1
                        FROM entries
                        WHERE id = ? AND user_id = ?
                    )
                    """,
                [
                    (
                        vector_id,
                        entry_id,
                        record_user_id,
                        chunk_index,
                        dim,
                        nonce,
                        ct,
                        alg,
                        record_dtype,
                        entry_id,
                        record_user_id,
                    )
                    for (
                        vector_id,
                        entry_id,
                        record_user_id,
                        chunk_index,
                        dim,
                        nonce,
                        ct,
                        alg,
                        record_dtype,
                    ) in records
                ],
            )

    async def get_latest_vectors(
        self, user_id: str, limit: int, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT v.id, v.entry_id, v.dim, v.nonce, v.ciphertext, v.alg, v.dtype, m.created_at
                FROM vectors v
                JOIN entries m ON v.entry_id = m.id AND m.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()

        return await asyncio.to_thread(
            self._decrypt_vector_rows,
            rows,
            dek,
            user_id,
        )

    async def get_vectors_older_than(
        self, user_id: str, before_id: str, limit: int, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT v.id, v.entry_id, v.dim, v.nonce, v.ciphertext, v.alg, v.dtype, m.created_at
                FROM vectors v
                JOIN entries m ON v.entry_id = m.id AND m.user_id = ?
                WHERE m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, before_id, limit),
            )
            rows = await cursor.fetchall()

        return await asyncio.to_thread(
            self._decrypt_vector_rows,
            rows,
            dek,
            user_id,
        )

    def _prepare_encrypted_vector(
        self,
        vec: np.ndarray,
        ctx: EncryptionContext,
        entry_id: str,
        vector_id: str,
        dtype: str,
    ) -> tuple[int, bytes, bytes, bytes]:
        np_dtype = np.float16 if dtype == "float16" else np.float32
        vec_arr = np.asarray(vec, dtype=np_dtype)
        dim = int(vec_arr.shape[0])
        nonce, ct, alg = self._encrypt_vector(
            ctx,
            entry_id,
            vector_id,
            vec_arr.tobytes(),
        )
        return dim, nonce, ct, alg

    def _prepare_batch_records(
        self,
        vectors: list[tuple[str, str, int, np.ndarray]],
        ctx: EncryptionContext,
        dtype: str,
    ) -> list[tuple[str, str, str, int, int, bytes, bytes, bytes, str]]:
        records: list[tuple[str, str, str, int, int, bytes, bytes, bytes, str]] = []
        np_dtype = np.float16 if dtype == "float16" else np.float32
        for vector_id, entry_id, chunk_index, vec in vectors:
            vec_arr = np.asarray(vec, dtype=np_dtype).ravel()
            dim = int(vec_arr.shape[0])
            nonce, ct, alg = self._encrypt_vector(
                ctx,
                entry_id,
                vector_id,
                vec_arr.tobytes(),
            )
            records.append(
                (
                    vector_id,
                    entry_id,
                    ctx.user_id,
                    chunk_index,
                    dim,
                    nonce,
                    ct,
                    alg,
                    dtype,
                )
            )
        return records

    def _decrypt_vector_rows(self, rows, dek: bytes, user_id: str) -> list[dict]:
        vectors: list[dict] = []
        for row in rows:
            dtype = None
            try:
                if "dtype" in row.keys():
                    dtype = row["dtype"]
            except AttributeError:
                dtype = row.get("dtype") if isinstance(row, dict) else None
            dtype = (dtype or "float32").lower()
            np_dtype = np.float16 if dtype == "float16" else np.float32
            vec_bytes = self._decrypt_vector(
                dek,
                user_id,
                row["entry_id"],
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            vec = np.frombuffer(vec_bytes, dtype=np_dtype).reshape(row["dim"])
            if np_dtype == np.float16:
                vec = vec.astype(np.float32)
            vectors.append(
                {
                    "id": row["id"],
                    "entry_id": row["entry_id"],
                    "created_at": row["created_at"],
                    "vec": vec,
                }
            )
        return vectors
