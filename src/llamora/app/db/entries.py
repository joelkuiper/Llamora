from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import orjson
from aiosqlitepool import SQLiteConnectionPool
from ulid import ULID

from llamora.llm.tokenizers.tokenizer import count_message_tokens

from llamora.app.services.history_cache import HistoryCache

from .base import BaseRepository
from .events import RepositoryEventBus, ENTRY_HISTORY_CHANGED_EVENT
from .utils import cached_tag_name

EntryAppendedCallback = Callable[[str, str, str, bytes], Awaitable[None]]


class EntriesRepository(BaseRepository):
    """Persistence helpers for encrypted diary entries."""

    def __init__(
        self,
        pool: SQLiteConnectionPool,
        encrypt_message,
        decrypt_message,
        event_bus: RepositoryEventBus | None = None,
        history_cache: HistoryCache | None = None,
    ) -> None:
        super().__init__(pool)
        self._encrypt_message = encrypt_message
        self._decrypt_message = decrypt_message
        self._on_entry_appended: EntryAppendedCallback | None = None
        self._event_bus = event_bus
        if history_cache is None:
            raise ValueError("history_cache must be provided")
        self._history_cache = history_cache

    def set_on_entry_appended(self, callback: EntryAppendedCallback | None) -> None:
        self._on_entry_appended = callback

    async def _get_cached_history(
        self, user_id: str, created_date: str
    ) -> list[dict] | None:
        return await self._history_cache.get(user_id, created_date)

    async def _store_history_cache(
        self, user_id: str, created_date: str, history: list[dict]
    ) -> None:
        await self._history_cache.store(user_id, created_date, history)

    def _rows_to_entries(
        self, rows, user_id: str, dek: bytes
    ) -> list[dict]:  # pragma: no cover - trivial helper
        entries: list[dict] = []
        for row in rows:
            created_date = None
            if "created_date" in row.keys():
                created_date = row["created_date"]
            record_json = self._decrypt_message(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            rec = orjson.loads(record_json)
            entries.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "created_date": created_date,
                    "role": row["role"],
                    "reply_to": row["reply_to"],
                    "text": rec.get("text", ""),
                    "meta": rec.get("meta", {}),
                    "prompt_tokens": int(row["prompt_tokens"] or 0),
                }
            )
        return entries

    def _rows_to_history(self, rows, user_id: str, dek: bytes) -> list[dict]:
        history: list[dict] = []
        current: dict | None = None
        for row in rows:
            entry_id = row["id"]
            if not history or history[-1]["id"] != entry_id:
                record_json = self._decrypt_message(
                    dek,
                    user_id,
                    entry_id,
                    row["nonce"],
                    row["ciphertext"],
                    row["msg_alg"],
                )
                rec = orjson.loads(record_json)
                current = {
                    "id": entry_id,
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "reply_to": row["reply_to"],
                    "text": rec.get("text", ""),
                    "meta": rec.get("meta", {}),
                    "prompt_tokens": int(row["prompt_tokens"] or 0),
                    "tags": [],
                }
                history.append(current)
            if row["tag_hash"] is not None and current is not None:
                tag_name = cached_tag_name(
                    user_id,
                    row["tag_hash"],
                    row["name_nonce"],
                    row["name_ct"],
                    row["tag_alg"].encode(),
                    dek,
                    self._decrypt_message,
                )
                current["tags"].append(
                    {"name": tag_name, "hash": row["tag_hash"].hex()}
                )
        return history

    @staticmethod
    def _thread_entries(history: list[dict]) -> list[dict]:
        entries: list[dict] = []
        by_user_id: dict[str, dict] = {}

        for entry in history:
            role = entry.get("role")
            entry_id = str(entry.get("id") or "")
            reply_to = entry.get("reply_to")
            reply_key = str(reply_to) if reply_to else ""

            if role == "user":
                entry_item = {"entry": entry, "responses": []}
                entries.append(entry_item)
                if entry_id:
                    by_user_id[entry_id] = entry_item
                continue

            if reply_key and reply_key in by_user_id:
                by_user_id[reply_key]["responses"].append(entry)
                continue

            entries.append({"entry": entry, "responses": []})

        return entries

    async def append_entry(
        self,
        user_id: str,
        role: str,
        content: str,
        dek: bytes,
        meta: dict | None = None,
        reply_to: str | None = None,
        created_date: str | None = None,
    ) -> str:
        entry_id = str(ULID())
        record = {"text": content, "meta": meta or {}}
        plaintext = orjson.dumps(record).decode()
        nonce, ct, alg = self._encrypt_message(dek, user_id, entry_id, plaintext)
        prompt_tokens = await asyncio.to_thread(
            count_message_tokens, role, record.get("text", "")
        )

        async with self.pool.connection() as conn:
            columns = [
                "id",
                "user_id",
                "role",
                "reply_to",
                "nonce",
                "ciphertext",
                "alg",
                "prompt_tokens",
            ]
            params: list = [
                entry_id,
                user_id,
                role,
                reply_to,
                nonce,
                ct,
                alg,
                prompt_tokens,
            ]

            if created_date:
                columns.append("created_date")
                params.append(created_date)

            placeholders = ", ".join(["?"] * len(columns))
            sql = (
                f"INSERT INTO entries ({', '.join(columns)}) "
                f"VALUES ({placeholders}) "
                "RETURNING created_at, created_date"
            )

            async def _execute_and_fetch():
                cursor = await conn.execute(sql, tuple(params))
                row = await cursor.fetchone()
                await cursor.close()
                return row

            row = await self._run_in_transaction(conn, _execute_and_fetch)

        created_at = row["created_at"] if row else None
        created_date = row["created_date"] if row else created_date

        entry_record = {
            "id": entry_id,
            "created_at": created_at,
            "role": role,
            "reply_to": reply_to,
            "text": record.get("text", ""),
            "meta": record.get("meta", {}),
            "prompt_tokens": prompt_tokens,
            "tags": [],
        }

        if created_date and self._event_bus:
            await self._event_bus.emit_for_entry_date(
                ENTRY_HISTORY_CHANGED_EVENT,
                user_id=user_id,
                created_date=created_date,
                entry_id=entry_id,
                reason="insert",
                entry=entry_record,
            )

        if self._on_entry_appended:
            await self._on_entry_appended(user_id, entry_id, plaintext, dek)

        return entry_id

    async def entry_exists(self, user_id: str, entry_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM entries WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            )
            row = await cursor.fetchone()
        return bool(row)

    async def delete_entry(self, user_id: str, entry_id: str) -> list[str]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, role, created_date FROM entries WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            )
            row = await cursor.fetchone()
            if not row:
                return []

            delete_ids = [row["id"]]
            created_dates = {row["created_date"]} if row["created_date"] else set()

            if row["role"] == "user":
                reply_cursor = await conn.execute(
                    """
                    SELECT id, created_date
                    FROM entries
                    WHERE user_id = ? AND reply_to = ?
                    """,
                    (user_id, entry_id),
                )
                reply_rows = await reply_cursor.fetchall()
                for reply_row in reply_rows:
                    delete_ids.append(reply_row["id"])
                    if reply_row["created_date"]:
                        created_dates.add(reply_row["created_date"])

            placeholders = ",".join("?" for _ in delete_ids)

            async def _execute_deletes():
                await conn.execute(
                    f"""
                    DELETE FROM tag_entry_xref
                    WHERE user_id = ? AND entry_id IN ({placeholders})
                    """,
                    (user_id, *delete_ids),
                )
                await conn.execute(
                    f"""
                    DELETE FROM vectors
                    WHERE user_id = ? AND id IN ({placeholders})
                    """,
                    (user_id, *delete_ids),
                )
                await conn.execute(
                    f"""
                    DELETE FROM entries
                    WHERE user_id = ? AND id IN ({placeholders})
                    """,
                    (user_id, *delete_ids),
                )

            await self._run_in_transaction(conn, _execute_deletes)

        if self._event_bus:
            for created_date in created_dates:
                await self._event_bus.emit_for_entry_date(
                    ENTRY_HISTORY_CHANGED_EVENT,
                    user_id=user_id,
                    created_date=created_date,
                    entry_id=entry_id,
                    reason="delete",
                )

        return delete_ids

    async def update_entry_text(
        self,
        user_id: str,
        entry_id: str,
        text: str,
        dek: bytes,
        *,
        meta: dict | None = None,
    ) -> dict | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, role, reply_to, nonce, ciphertext, alg, created_at, created_date
                FROM entries
                WHERE id = ? AND user_id = ?
                """,
                (entry_id, user_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if not row:
                return None

            if meta is None:
                record_json = self._decrypt_message(
                    dek,
                    user_id,
                    row["id"],
                    row["nonce"],
                    row["ciphertext"],
                    row["alg"],
                )
                record = orjson.loads(record_json)
                meta = record.get("meta", {})

            record = {"text": text, "meta": meta or {}}
            plaintext = orjson.dumps(record).decode()
            nonce, ct, alg = self._encrypt_message(dek, user_id, entry_id, plaintext)
            prompt_tokens = await asyncio.to_thread(
                count_message_tokens, row["role"], record.get("text", "")
            )

            async def _execute_update():
                await conn.execute(
                    """
                    UPDATE entries
                    SET nonce = ?, ciphertext = ?, alg = ?, prompt_tokens = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (nonce, ct, alg, prompt_tokens, entry_id, user_id),
                )

            await self._run_in_transaction(conn, _execute_update)

        entry_record = {
            "id": entry_id,
            "created_at": row["created_at"],
            "created_date": row["created_date"],
            "role": row["role"],
            "reply_to": row["reply_to"],
            "text": record.get("text", ""),
            "meta": record.get("meta", {}),
            "prompt_tokens": prompt_tokens,
        }

        if row["created_date"] and self._event_bus:
            await self._event_bus.emit_for_entry_date(
                ENTRY_HISTORY_CHANGED_EVENT,
                user_id=user_id,
                created_date=row["created_date"],
                entry_id=entry_id,
                reason="update",
                entry=entry_record,
            )

        return entry_record

    async def get_entry_date(self, user_id: str, entry_id: str) -> str | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT created_date FROM entries WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            )
            row = await cursor.fetchone()
        return row["created_date"] if row else None

    async def invalidate_history_for_entry(
        self, user_id: str, entry_id: str, *, reason: str = "invalidate"
    ) -> None:
        created_date = await self.get_entry_date(user_id, entry_id)
        if not created_date:
            return

        if self._event_bus:
            await self._event_bus.emit_for_entry_date(
                ENTRY_HISTORY_CHANGED_EVENT,
                user_id=user_id,
                created_date=created_date,
                entry_id=entry_id,
                reason=reason,
            )

    async def get_latest_entries(
        self, user_id: str, limit: int, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg,
                       m.created_at, m.created_date, m.prompt_tokens
                FROM entries m
                WHERE m.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, user_id, dek)

    async def get_entries_older_than(
        self, user_id: str, before_id: str, limit: int, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg,
                       m.created_at, m.created_date, m.prompt_tokens
                FROM entries m
                WHERE m.user_id = ? AND m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, before_id, limit),
            )
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, user_id, dek)

    async def get_user_latest_entry_id(self, user_id: str) -> str | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id
                FROM entries m
                WHERE m.user_id = ?
                ORDER BY m.id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
        return row["id"] if row else None

    async def get_entries_by_ids(
        self, user_id: str, ids: list[str], dek: bytes
    ) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT m.id, m.created_at, m.created_date, m.role, m.reply_to,
                       m.nonce, m.ciphertext, m.alg, m.prompt_tokens
                FROM entries m
                WHERE m.user_id = ? AND m.id IN ({placeholders})
                """,
                (user_id, *ids),
            )
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, user_id, dek)

    async def get_entries_for_date(
        self, user_id: str, created_date: str, dek: bytes
    ) -> list[dict]:
        history = await self.get_flat_entries_for_date(user_id, created_date, dek)
        return self._thread_entries(list(history))

    async def get_flat_entries_for_date(
        self, user_id: str, created_date: str, dek: bytes
    ) -> list[dict]:
        cached = await self._get_cached_history(user_id, created_date)
        if cached is not None:
            return list(cached)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.created_at, m.role, m.reply_to, m.nonce,
                       m.ciphertext, m.alg AS msg_alg,
                       m.prompt_tokens,
                       x.ulid AS tag_ulid,
                       t.tag_hash, t.name_ct, t.name_nonce, t.alg AS tag_alg
                FROM entries m
                LEFT JOIN tag_entry_xref x ON x.entry_id = m.id AND x.user_id = ?
                LEFT JOIN tags t ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                WHERE m.user_id = ? AND m.created_date = ?
                ORDER BY m.id ASC, x.ulid ASC
                """,
                (user_id, user_id, created_date),
            )
            rows = await cursor.fetchall()

        history = self._rows_to_history(rows, user_id, dek)
        await self._store_history_cache(user_id, created_date, history)
        return history

    async def get_recent_entries(
        self, user_id: str, created_date: str, dek: bytes, limit: int
    ) -> list[dict]:
        if limit <= 0:
            return []

        cached = await self._get_cached_history(user_id, created_date)
        if cached is not None:
            return list(cached[-limit:])

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                WITH recent AS (
                    SELECT m.id, m.created_at, m.role, m.reply_to, m.nonce,
                           m.ciphertext, m.alg, m.prompt_tokens
                    FROM entries m
                    WHERE m.user_id = ? AND m.created_date = ?
                    ORDER BY m.id DESC
                    LIMIT ?
                )
                SELECT recent.id, recent.created_at, recent.role, recent.reply_to,
                       recent.nonce, recent.ciphertext, recent.alg AS msg_alg,
                       recent.prompt_tokens,
                       x.ulid AS tag_ulid,
                       t.tag_hash, t.name_ct, t.name_nonce, t.alg AS tag_alg
                FROM recent
                LEFT JOIN tag_entry_xref x
                    ON x.entry_id = recent.id AND x.user_id = ?
                LEFT JOIN tags t
                    ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                ORDER BY recent.id ASC, x.ulid ASC
                """,
                (user_id, created_date, limit, user_id),
            )
            rows = await cursor.fetchall()

        return self._rows_to_history(rows, user_id, dek)

    async def get_days_with_entries(
        self, user_id: str, year: int, month: int
    ) -> list[int]:
        month_prefix = f"{year:04d}-{month:02d}"
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT CAST(substr(created_date, 9, 2) AS INTEGER) AS day
                FROM entries
                WHERE user_id = ? AND substr(created_date, 1, 7) = ?
                """,
                (user_id, month_prefix),
            )
            rows = await cursor.fetchall()
        return [row["day"] for row in rows]

    async def get_first_entry_date(self, user_id: str) -> str | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT MIN(created_date) AS first_date
                FROM entries
                WHERE user_id = ? AND created_date IS NOT NULL
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return row["first_date"] or None

    async def user_has_entries(self, user_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM entries WHERE user_id = ? LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
        return bool(row)
