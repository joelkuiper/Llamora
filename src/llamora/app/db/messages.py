from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Sequence, cast

import orjson
from aiosqlitepool import SQLiteConnectionPool
from ulid import ULID

from cachetools import TTLCache

from llamora.llm.tokenizers.tokenizer import count_message_tokens

from llamora.settings import settings

from .base import BaseRepository
from .events import (
    RepositoryEventBus,
    MESSAGE_HISTORY_CHANGED_EVENT,
    MESSAGE_TAGS_CHANGED_EVENT,
)
from .utils import cached_tag_name

MessageAppendedCallback = Callable[[str, str, str, bytes], Awaitable[None]]

FrozenHistory = tuple[Mapping[str, Any], ...]
_INVALID_HISTORY_SENTINEL = object()


def _freeze_history(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> FrozenHistory:
    frozen_messages: list[Mapping[str, Any]] = []
    for message in history:
        # Normalise to a plain dict so the cached payload cannot be mutated.
        raw_message = dict(message)
        tags = raw_message.get("tags") or []
        raw_message["tags"] = tuple(MappingProxyType(dict(tag)) for tag in tags)
        raw_message["meta"] = MappingProxyType(dict(raw_message.get("meta") or {}))
        frozen_messages.append(MappingProxyType(raw_message))
    return tuple(frozen_messages)


def _thaw_history(history: FrozenHistory) -> list[dict[str, Any]]:
    thawed: list[dict[str, Any]] = []
    for message in history:
        hydrated = dict(message)
        hydrated["tags"] = [dict(tag) for tag in message.get("tags", ())]
        hydrated["meta"] = dict(message.get("meta", {}))
        thawed.append(hydrated)
    return thawed


class MessagesRepository(BaseRepository):
    """Persistence helpers for encrypted chat messages."""

    def __init__(
        self,
        pool: SQLiteConnectionPool,
        encrypt_message,
        decrypt_message,
        event_bus: RepositoryEventBus | None = None,
    ) -> None:
        super().__init__(pool)
        self._encrypt_message = encrypt_message
        self._decrypt_message = decrypt_message
        self._on_message_appended: MessageAppendedCallback | None = None
        self._event_bus = event_bus
        history_cache_cfg = settings.MESSAGES.history_cache
        self._history_cache: TTLCache[tuple[str, str], FrozenHistory | object] = (
            TTLCache(
                maxsize=int(history_cache_cfg.maxsize),
                ttl=int(history_cache_cfg.ttl),
            )
        )
        self._history_cache_lock = asyncio.Lock()
        if self._event_bus:
            self._event_bus.subscribe(
                MESSAGE_TAGS_CHANGED_EVENT, self._handle_message_tags_changed
            )

    def set_on_message_appended(self, callback: MessageAppendedCallback | None) -> None:
        self._on_message_appended = callback

    async def _get_cached_history(
        self, user_id: str, created_date: str
    ) -> list[dict] | None:
        key = (user_id, created_date)
        async with self._history_cache_lock:
            cached = self._history_cache.get(key)

        if cached is None or cached is _INVALID_HISTORY_SENTINEL:
            return None

        frozen = cast(FrozenHistory, cached)
        return _thaw_history(frozen)

    async def _store_history_cache(
        self, user_id: str, created_date: str, history: list[dict]
    ) -> None:
        frozen = _freeze_history(history)
        async with self._history_cache_lock:
            self._history_cache[(user_id, created_date)] = frozen

    async def _append_message_to_cache(
        self, user_id: str, created_date: str, message: dict
    ) -> None:
        key = (user_id, created_date)
        while True:
            async with self._history_cache_lock:
                cached = self._history_cache.get(key)

            if cached is None or cached is _INVALID_HISTORY_SENTINEL:
                return

            frozen = cast(FrozenHistory, cached)
            history = _thaw_history(frozen)

            new_entry = dict(message)
            new_entry["tags"] = list(new_entry.get("tags", []))
            new_id = new_entry.get("id")
            inserted = False

            for idx, existing in enumerate(history):
                existing_id = existing.get("id")
                if existing_id == new_id:
                    history[idx] = new_entry
                    inserted = True
                    break
                if existing_id and new_id and existing_id > new_id:
                    history.insert(idx, new_entry)
                    inserted = True
                    break

            if not inserted:
                history.append(new_entry)

            updated = _freeze_history(history)

            async with self._history_cache_lock:
                current = self._history_cache.get(key)
                if current is cached:
                    self._history_cache[key] = updated
                    return

    async def _invalidate_history_cache(self, user_id: str, created_date: str) -> None:
        async with self._history_cache_lock:
            self._history_cache[(user_id, created_date)] = _INVALID_HISTORY_SENTINEL

    def _rows_to_messages(
        self, rows, user_id: str, dek: bytes
    ) -> list[dict]:  # pragma: no cover - trivial helper
        messages: list[dict] = []
        for row in rows:
            record_json = self._decrypt_message(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            rec = orjson.loads(record_json)
            messages.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "reply_to": row["reply_to"],
                    "message": rec.get("message", ""),
                    "meta": rec.get("meta", {}),
                    "prompt_tokens": int(row["prompt_tokens"] or 0),
                }
            )
        return messages

    def _rows_to_history(self, rows, user_id: str, dek: bytes) -> list[dict]:
        history: list[dict] = []
        current: dict | None = None
        for row in rows:
            msg_id = row["id"]
            if not history or history[-1]["id"] != msg_id:
                record_json = self._decrypt_message(
                    dek,
                    user_id,
                    msg_id,
                    row["nonce"],
                    row["ciphertext"],
                    row["msg_alg"],
                )
                rec = orjson.loads(record_json)
                current = {
                    "id": msg_id,
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "reply_to": row["reply_to"],
                    "message": rec.get("message", ""),
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

    async def append_message(
        self,
        user_id: str,
        role: str,
        message: str,
        dek: bytes,
        meta: dict | None = None,
        reply_to: str | None = None,
        created_date: str | None = None,
    ) -> str:
        msg_id = str(ULID())
        record = {"message": message, "meta": meta or {}}
        plaintext = orjson.dumps(record).decode()
        nonce, ct, alg = self._encrypt_message(dek, user_id, msg_id, plaintext)
        prompt_tokens = await asyncio.to_thread(
            count_message_tokens, role, record.get("message", "")
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
                msg_id,
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
                f"INSERT INTO messages ({', '.join(columns)}) "
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

        message_entry = {
            "id": msg_id,
            "created_at": created_at,
            "role": role,
            "reply_to": reply_to,
            "message": record.get("message", ""),
            "meta": record.get("meta", {}),
            "prompt_tokens": prompt_tokens,
            "tags": [],
        }

        if created_date:
            await self._append_message_to_cache(user_id, created_date, message_entry)
            if self._event_bus:
                await self._event_bus.emit_for_message_date(
                    MESSAGE_HISTORY_CHANGED_EVENT,
                    user_id=user_id,
                    created_date=created_date,
                    message_id=msg_id,
                    reason="insert",
                )

        if self._on_message_appended:
            await self._on_message_appended(user_id, msg_id, plaintext, dek)

        return msg_id

    async def message_exists(self, user_id: str, message_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM messages WHERE id = ? AND user_id = ?",
                (message_id, user_id),
            )
            row = await cursor.fetchone()
        return bool(row)

    async def get_message_date(self, user_id: str, message_id: str) -> str | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT created_date FROM messages WHERE id = ? AND user_id = ?",
                (message_id, user_id),
            )
            row = await cursor.fetchone()
        return row["created_date"] if row else None

    async def invalidate_history_for_message(
        self, user_id: str, message_id: str, *, reason: str = "invalidate"
    ) -> None:
        created_date = await self.get_message_date(user_id, message_id)
        if not created_date:
            return

        await self._invalidate_history_cache(user_id, created_date)

        if self._event_bus:
            await self._event_bus.emit_for_message_date(
                MESSAGE_HISTORY_CHANGED_EVENT,
                user_id=user_id,
                created_date=created_date,
                message_id=message_id,
                reason=reason,
            )

    async def get_message_with_reply(
        self, user_id: str, message_id: str
    ) -> dict | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.created_date, reply.id AS reply_id
                FROM messages m
                LEFT JOIN messages reply
                    ON reply.reply_to = m.id
                    AND reply.user_id = m.user_id
                    AND reply.role = 'assistant'
                WHERE m.user_id = ? AND m.id = ?
                ORDER BY reply.created_at ASC
                LIMIT 1
                """,
                (user_id, message_id),
            )
            row = await cursor.fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "created_date": row["created_date"],
            "reply_id": row["reply_id"],
        }

    async def get_latest_messages(
        self, user_id: str, limit: int, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg,
                       m.created_at, m.prompt_tokens
                FROM messages m
                WHERE m.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()

        return self._rows_to_messages(rows, user_id, dek)

    async def get_messages_older_than(
        self, user_id: str, before_id: str, limit: int, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg,
                       m.created_at, m.prompt_tokens
                FROM messages m
                WHERE m.user_id = ? AND m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, before_id, limit),
            )
            rows = await cursor.fetchall()

        return self._rows_to_messages(rows, user_id, dek)

    async def get_user_latest_id(self, user_id: str) -> str | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id
                FROM messages m
                WHERE m.user_id = ?
                ORDER BY m.id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
        return row["id"] if row else None

    async def get_messages_by_ids(
        self, user_id: str, ids: list[str], dek: bytes
    ) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT m.id, m.created_at, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg,
                       m.prompt_tokens
                FROM messages m
                WHERE m.user_id = ? AND m.id IN ({placeholders})
                """,
                (user_id, *ids),
            )
            rows = await cursor.fetchall()

        return self._rows_to_messages(rows, user_id, dek)

    async def get_history(
        self, user_id: str, created_date: str, dek: bytes
    ) -> list[dict]:
        cached = await self._get_cached_history(user_id, created_date)
        if cached is not None:
            return cached

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.created_at, m.role, m.reply_to, m.nonce,
                       m.ciphertext, m.alg AS msg_alg,
                       m.prompt_tokens,
                       x.ulid AS tag_ulid,
                       t.tag_hash, t.name_ct, t.name_nonce, t.alg AS tag_alg
                FROM messages m
                LEFT JOIN tag_message_xref x ON x.message_id = m.id AND x.user_id = ?
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

    async def get_recent_history(
        self, user_id: str, created_date: str, dek: bytes, limit: int
    ) -> list[dict]:
        if limit <= 0:
            return []

        cached = await self._get_cached_history(user_id, created_date)
        if cached is not None:
            return cached[-limit:]

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                WITH recent AS (
                    SELECT m.id, m.created_at, m.role, m.reply_to, m.nonce,
                           m.ciphertext, m.alg, m.prompt_tokens
                    FROM messages m
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
                LEFT JOIN tag_message_xref x
                    ON x.message_id = recent.id AND x.user_id = ?
                LEFT JOIN tags t
                    ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                ORDER BY recent.id ASC, x.ulid ASC
                """,
                (user_id, created_date, limit, user_id),
            )
            rows = await cursor.fetchall()

        return self._rows_to_history(rows, user_id, dek)

    async def get_days_with_messages(
        self, user_id: str, year: int, month: int
    ) -> list[int]:
        month_prefix = f"{year:04d}-{month:02d}"
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT CAST(substr(created_date, 9, 2) AS INTEGER) AS day
                FROM messages
                WHERE user_id = ? AND substr(created_date, 1, 7) = ?
                """,
                (user_id, month_prefix),
            )
            rows = await cursor.fetchall()
        return [row["day"] for row in rows]

    async def user_has_messages(self, user_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM messages WHERE user_id = ? LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
        return bool(row)

    async def _handle_message_tags_changed(
        self, *, user_id: str, message_id: str
    ) -> None:
        await self.invalidate_history_for_message(
            user_id,
            message_id,
            reason="tags-changed",
        )
