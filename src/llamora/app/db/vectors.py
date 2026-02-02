from __future__ import annotations

import asyncio

import numpy as np
from aiosqlitepool import SQLiteConnectionPool

from .base import BaseRepository


class VectorsRepository(BaseRepository):
    """Persistence helpers for encrypted vector embeddings."""

    def __init__(
        self, pool: SQLiteConnectionPool, encrypt_vector, decrypt_vector
    ) -> None:
        super().__init__(pool)
        self._encrypt_vector = encrypt_vector
        self._decrypt_vector = decrypt_vector

    async def store_vector(
        self, msg_id: str, user_id: str, vec: np.ndarray, dek: bytes
    ) -> None:
        dim, nonce, ct, alg = await asyncio.to_thread(
            self._prepare_encrypted_vector,
            vec,
            dek,
            user_id,
            msg_id,
        )
        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.execute,
                """
                    INSERT OR REPLACE INTO vectors (id, user_id, dim, nonce, ciphertext, alg)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                (msg_id, user_id, dim, nonce, ct, alg),
            )

    async def store_vectors_batch(
        self,
        user_id: str,
        vectors: list[tuple[str, np.ndarray]],
        dek: bytes,
    ) -> None:
        if not vectors:
            return

        records = await asyncio.to_thread(
            self._prepare_batch_records,
            vectors,
            dek,
            user_id,
        )

        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.executemany,
                """
                    INSERT OR REPLACE INTO vectors (id, user_id, dim, nonce, ciphertext, alg)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                records,
            )

    async def get_latest_vectors(
        self, user_id: str, limit: int, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT v.id, v.dim, v.nonce, v.ciphertext, v.alg, m.created_at
                FROM vectors v
                JOIN entries m ON v.id = m.id AND m.user_id = ?
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
                SELECT v.id, v.dim, v.nonce, v.ciphertext, v.alg, m.created_at
                FROM vectors v
                JOIN entries m ON v.id = m.id AND m.user_id = ?
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
        self, vec: np.ndarray, dek: bytes, user_id: str, msg_id: str
    ) -> tuple[int, bytes, bytes, bytes]:
        vec_arr = np.asarray(vec, dtype=np.float32)
        dim = int(vec_arr.shape[0])
        nonce, ct, alg = self._encrypt_vector(
            dek,
            user_id,
            msg_id,
            vec_arr.tobytes(),
        )
        return dim, nonce, ct, alg

    def _prepare_batch_records(
        self,
        vectors: list[tuple[str, np.ndarray]],
        dek: bytes,
        user_id: str,
    ) -> list[tuple[str, str, int, bytes, bytes, bytes]]:
        records: list[tuple[str, str, int, bytes, bytes, bytes]] = []
        for msg_id, vec in vectors:
            vec_arr = np.asarray(vec, dtype=np.float32).ravel()
            dim = int(vec_arr.shape[0])
            nonce, ct, alg = self._encrypt_vector(
                dek,
                user_id,
                msg_id,
                vec_arr.tobytes(),
            )
            records.append((msg_id, user_id, dim, nonce, ct, alg))
        return records

    def _decrypt_vector_rows(self, rows, dek: bytes, user_id: str) -> list[dict]:
        vectors: list[dict] = []
        for row in rows:
            vec_bytes = self._decrypt_vector(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            vec = np.frombuffer(vec_bytes, dtype=np.float32).reshape(row["dim"])
            vectors.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "vec": vec,
                }
            )
        return vectors
