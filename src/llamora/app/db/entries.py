from __future__ import annotations

import asyncio
import re
from datetime import date as _date_type
from typing import Awaitable, Callable, Iterable, Mapping

import orjson
from aiosqlitepool import SQLiteConnectionPool
from ulid import ULID

from llamora.llm.tokenizers.tokenizer import count_message_tokens

from llamora.app.services.history_cache import HistoryCache
from llamora.app.services.crypto import CryptoContext
from llamora.app.services.digest_policy import ENTRY_DIGEST_VERSION, day_digest

from .base import BaseRepository
from .events import (
    ENTRY_DELETED_EVENT,
    ENTRY_INSERTED_EVENT,
    ENTRY_UPDATED_EVENT,
    RepositoryEventBus,
)
from .utils import cached_tag_name, get_month_bounds

EntryAppendedCallback = Callable[[CryptoContext, str, str], Awaitable[None]]

_FLAG_PATTERN = re.compile(r"^[a-z0-9_]+$")
_AUTO_OPENING_FLAG = "auto_opening"


def parse_entry_flags(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part for part in value.strip("|").split("|") if part}


def normalize_entry_flags(flags: Iterable[str]) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in flags:
        text = str(raw or "").strip().lower()
        if not text:
            continue
        if not _FLAG_PATTERN.fullmatch(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if not cleaned:
        return ""
    cleaned.sort()
    return f"|{'|'.join(cleaned)}|"


def build_entry_flags_from_meta(
    meta: Mapping[str, object] | None, existing: Iterable[str] | None = None
) -> str:
    flags: set[str] = set(existing or [])
    if meta and meta.get("auto_opening"):
        flags.add(_AUTO_OPENING_FLAG)
    return normalize_entry_flags(flags)


class EntriesRepository(BaseRepository):
    """Persistence helpers for encrypted diary entries."""

    def __init__(
        self,
        pool: SQLiteConnectionPool,
        event_bus: RepositoryEventBus | None = None,
        history_cache: HistoryCache | None = None,
    ) -> None:
        super().__init__(pool)
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
        self, rows, ctx: CryptoContext
    ) -> list[dict]:  # pragma: no cover - trivial helper
        entries: list[dict] = []
        for row in rows:
            created_date = None
            updated_at = None
            digest = None
            if "created_date" in row.keys():
                created_date = row["created_date"]
            if "updated_at" in row.keys():
                updated_at = row["updated_at"]
            if "digest" in row.keys():
                digest = row["digest"]
            record_json = ctx.decrypt_entry(
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
                    "updated_at": updated_at,
                    "created_date": created_date,
                    "role": row["role"],
                    "reply_to": row["reply_to"],
                    "text": rec.get("text", ""),
                    "meta": rec.get("meta", {}),
                    "prompt_tokens": int(row["prompt_tokens"] or 0),
                    "digest": digest,
                }
            )
        return entries

    def _rows_to_history(self, rows, ctx: CryptoContext) -> list[dict]:
        history: list[dict] = []
        current: dict | None = None
        for row in rows:
            entry_id = row["id"]
            if not history or history[-1]["id"] != entry_id:
                updated_at = None
                if "updated_at" in row.keys():
                    updated_at = row["updated_at"]
                record_json = ctx.decrypt_entry(
                    entry_id,
                    row["nonce"],
                    row["ciphertext"],
                    row["msg_alg"],
                )
                rec = orjson.loads(record_json)
                current = {
                    "id": entry_id,
                    "created_at": row["created_at"],
                    "updated_at": updated_at,
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
                    ctx,
                    row["tag_hash"],
                    row["name_nonce"],
                    row["name_ct"],
                    row["tag_alg"].encode(),
                )
                current["tags"].append(
                    {"name": tag_name, "hash": row["tag_hash"].hex()}
                )
        return history

    @staticmethod
    def _normalize_tag_hashes(rows) -> tuple[str, ...]:
        seen: set[str] = set()
        values: list[str] = []
        for row in rows:
            raw = row["tag_hash"] if "tag_hash" in row.keys() else row["tag_hash_hex"]
            if raw is None:
                continue
            if isinstance(raw, bytes):
                value = raw.hex()
            else:
                value = str(raw).strip().lower()
            if not value or value in seen:
                continue
            seen.add(value)
            values.append(value)
        values.sort()
        return tuple(values)

    async def _get_tag_hashes_for_entry(
        self, user_id: str, entry_id: str
    ) -> tuple[str, ...]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT tag_hash
                FROM tag_entry_xref
                WHERE user_id = ? AND entry_id = ?
                """,
                (user_id, entry_id),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return self._normalize_tag_hashes(rows)

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

    @staticmethod
    def _require_entry_digest(
        ctx: CryptoContext, entry_id: str, role: str, text: str
    ) -> str:
        digest = ctx.entry_digest(entry_id, role, text)
        if not digest:
            raise ValueError(f"unable to compute digest for entry {entry_id}")
        return digest

    async def append_entry(
        self,
        ctx: CryptoContext,
        role: str,
        content: str,
        meta: dict | None = None,
        reply_to: str | None = None,
        created_at: str | None = None,
        created_date: str | None = None,
    ) -> str:
        ctx.require_write(operation="entries.append_entry")
        entry_id = str(ULID())
        record = {"text": content, "meta": meta or {}}
        plaintext = orjson.dumps(record).decode()
        nonce, ct, alg = ctx.encrypt_entry(entry_id, plaintext)
        prompt_tokens = await asyncio.to_thread(
            count_message_tokens, role, record.get("text", "")
        )
        digest = self._require_entry_digest(ctx, entry_id, role, record.get("text", ""))
        flags = build_entry_flags_from_meta(record.get("meta", {}))

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
                "digest",
                "digest_version",
                "flags",
            ]
            params: list = [
                entry_id,
                ctx.user_id,
                role,
                reply_to,
                nonce,
                ct,
                alg,
                prompt_tokens,
                digest,
                ENTRY_DIGEST_VERSION,
                flags,
            ]

            if created_at:
                columns.append("created_at")
                params.append(created_at)

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
                ENTRY_INSERTED_EVENT,
                user_id=ctx.user_id,
                created_date=created_date,
                entry_id=entry_id,
                entry=entry_record,
            )

        if self._on_entry_appended:
            await self._on_entry_appended(ctx, entry_id, record.get("text", ""))

        return entry_id

    async def entry_exists(self, user_id: str, entry_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM entries WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            )
            row = await cursor.fetchone()
        return bool(row)

    async def delete_entry(
        self, user_id: str, entry_id: str
    ) -> tuple[list[str], str | None]:
        tag_hashes: tuple[str, ...] = ()
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, role, created_date FROM entries WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            )
            row = await cursor.fetchone()
            if not row:
                return [], None

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
            tag_cursor = await conn.execute(
                f"""
                SELECT DISTINCT tag_hash
                FROM tag_entry_xref
                WHERE user_id = ? AND entry_id IN ({placeholders})
                """,
                (user_id, *delete_ids),
            )
            tag_rows = await tag_cursor.fetchall()
            await tag_cursor.close()
            tag_hashes = self._normalize_tag_hashes(tag_rows)

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
                    WHERE user_id = ? AND entry_id IN ({placeholders})
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
                    ENTRY_DELETED_EVENT,
                    user_id=user_id,
                    created_date=created_date,
                    entry_id=entry_id,
                    tag_hashes=tag_hashes,
                )

        return delete_ids, row["role"]

    async def update_entry_text(
        self,
        ctx: CryptoContext,
        entry_id: str,
        text: str,
        *,
        meta: dict | None = None,
    ) -> dict | None:
        ctx.require_write(operation="entries.update_entry_text")
        tag_hashes: tuple[str, ...] = ()
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, role, reply_to, nonce, ciphertext, alg, flags, created_at, updated_at, created_date
                FROM entries
                WHERE id = ? AND user_id = ?
                """,
                (entry_id, ctx.user_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if not row:
                return None

            if meta is None:
                record_json = ctx.decrypt_entry(
                    row["id"],
                    row["nonce"],
                    row["ciphertext"],
                    row["alg"],
                )
                record = orjson.loads(record_json)
                meta = record.get("meta", {})

            record = {"text": text, "meta": meta or {}}
            plaintext = orjson.dumps(record).decode()
            nonce, ct, alg = ctx.encrypt_entry(entry_id, plaintext)
            prompt_tokens = await asyncio.to_thread(
                count_message_tokens, row["role"], record.get("text", "")
            )
            digest = self._require_entry_digest(
                ctx, entry_id, row["role"], record.get("text", "")
            )
            flags = build_entry_flags_from_meta(
                meta or {}, parse_entry_flags(row["flags"])
            )

            async def _execute_update():
                cursor = await conn.execute(
                    """
                    UPDATE entries
                    SET nonce = ?,
                        ciphertext = ?,
                        alg = ?,
                        prompt_tokens = ?,
                        digest = ?,
                        digest_version = ?,
                        flags = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id = ?
                    RETURNING updated_at
                    """,
                    (
                        nonce,
                        ct,
                        alg,
                        prompt_tokens,
                        digest,
                        ENTRY_DIGEST_VERSION,
                        flags,
                        entry_id,
                        ctx.user_id,
                    ),
                )
                updated_row = await cursor.fetchone()
                await cursor.close()
                return updated_row

            updated_row = await self._run_in_transaction(conn, _execute_update)
            tag_cursor = await conn.execute(
                """
                SELECT DISTINCT tag_hash
                FROM tag_entry_xref
                WHERE user_id = ? AND entry_id = ?
                """,
                (ctx.user_id, entry_id),
            )
            tag_rows = await tag_cursor.fetchall()
            await tag_cursor.close()
            tag_hashes = self._normalize_tag_hashes(tag_rows)

        entry_record = {
            "id": entry_id,
            "created_at": row["created_at"],
            "updated_at": updated_row["updated_at"] if updated_row else None,
            "created_date": row["created_date"],
            "role": row["role"],
            "reply_to": row["reply_to"],
            "text": record.get("text", ""),
            "meta": record.get("meta", {}),
            "prompt_tokens": prompt_tokens,
            "tags": [{"hash": tag_hash} for tag_hash in tag_hashes],
        }

        if row["created_date"] and self._event_bus:
            await self._event_bus.emit_for_entry_date(
                ENTRY_UPDATED_EVENT,
                user_id=ctx.user_id,
                created_date=row["created_date"],
                entry_id=entry_id,
                entry=entry_record,
                tag_hashes=tag_hashes,
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
        tag_hashes = await self._get_tag_hashes_for_entry(user_id, entry_id)

        if self._event_bus:
            await self._event_bus.emit_for_entry_date(
                ENTRY_UPDATED_EVENT,
                user_id=user_id,
                created_date=created_date,
                entry_id=entry_id,
                tag_hashes=tag_hashes,
            )

    async def get_latest_entries(self, ctx: CryptoContext, limit: int) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg,
                       m.created_at, m.updated_at, m.created_date, m.prompt_tokens
                FROM entries m
                WHERE m.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (ctx.user_id, limit),
            )
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, ctx)

    async def get_entries_older_than(
        self, ctx: CryptoContext, before_id: str, limit: int
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg,
                       m.created_at, m.updated_at, m.created_date, m.prompt_tokens
                FROM entries m
                WHERE m.user_id = ? AND m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (ctx.user_id, before_id, limit),
            )
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, ctx)

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
        self, ctx: CryptoContext, ids: list[str]
    ) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT m.id, m.created_at, m.updated_at, m.created_date, m.role, m.reply_to,
                       m.nonce, m.ciphertext, m.alg, m.prompt_tokens, m.digest
                FROM entries m
                WHERE m.user_id = ? AND m.id IN ({placeholders})
                """,
                (ctx.user_id, *ids),
            )
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, ctx)

    async def get_recall_candidates_by_ids(
        self,
        ctx: CryptoContext,
        ids: list[str],
        *,
        max_entry_id: str | None = None,
        max_created_at: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return decrypted entry rows for recall planning in one query set."""

        if not ids:
            return []
        if limit is not None and limit <= 0:
            return []

        placeholders = ",".join("?" for _ in ids)
        params: list[object] = [ctx.user_id, *ids]
        conditions = [f"m.user_id = ? AND m.id IN ({placeholders})"]
        if max_entry_id:
            conditions.append("m.id <= ?")
            params.append(max_entry_id)
        if max_created_at:
            conditions.append("m.created_at <= ?")
            params.append(max_created_at)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT m.id, m.created_at, m.updated_at, m.created_date, m.role, m.reply_to,
                   m.nonce, m.ciphertext, m.alg, m.prompt_tokens, m.digest
            FROM entries m
            WHERE {where_clause}
            ORDER BY m.id DESC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, ctx)

    async def get_entries_by_reply_to_ids(
        self, ctx: CryptoContext, reply_to_ids: list[str]
    ) -> list[dict]:
        if not reply_to_ids:
            return []
        placeholders = ",".join("?" for _ in reply_to_ids)
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT m.id, m.created_at, m.updated_at, m.created_date, m.role, m.reply_to,
                       m.nonce, m.ciphertext, m.alg, m.prompt_tokens, m.digest
                FROM entries m
                WHERE m.user_id = ? AND m.reply_to IN ({placeholders})
                ORDER BY m.id ASC
                """,
                (ctx.user_id, *reply_to_ids),
            )
            rows = await cursor.fetchall()

        return self._rows_to_entries(rows, ctx)

    async def get_entries_for_date(
        self, ctx: CryptoContext, created_date: str
    ) -> list[dict]:
        history = await self.get_flat_entries_for_date(ctx, created_date)
        return self._thread_entries(list(history))

    async def get_flat_entries_for_date(
        self, ctx: CryptoContext, created_date: str
    ) -> list[dict]:
        cached = await self._get_cached_history(ctx.user_id, created_date)
        if cached is not None:
            return list(cached)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.created_at, m.updated_at, m.role, m.reply_to, m.nonce,
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
                (ctx.user_id, ctx.user_id, created_date),
            )
            rows = await cursor.fetchall()

        history = self._rows_to_history(rows, ctx)
        await self._store_history_cache(ctx.user_id, created_date, history)
        return history

    async def get_recent_entries(
        self, ctx: CryptoContext, created_date: str, limit: int
    ) -> list[dict]:
        if limit <= 0:
            return []

        cached = await self._get_cached_history(ctx.user_id, created_date)
        if cached is not None:
            return list(cached[-limit:])

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                WITH recent AS (
                    SELECT m.id, m.created_at, m.updated_at, m.role, m.reply_to, m.nonce,
                           m.ciphertext, m.alg, m.prompt_tokens
                    FROM entries m
                    WHERE m.user_id = ? AND m.created_date = ?
                    ORDER BY m.id DESC
                    LIMIT ?
                )
                SELECT recent.id, recent.created_at, recent.updated_at, recent.role, recent.reply_to,
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
                (ctx.user_id, created_date, limit, ctx.user_id),
            )
            rows = await cursor.fetchall()

        return self._rows_to_history(rows, ctx)

    async def get_days_with_entries(
        self, user_id: str, year: int, month: int
    ) -> tuple[list[int], list[int]]:
        month_start, next_month_start = get_month_bounds(year, month)
        active_days: set[int] = set()
        opening_only_days: set[int] = set()

        opening_like = f"%|{_AUTO_OPENING_FLAG}|%"
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT created_date AS created_date,
                       COUNT(*) AS total_entries,
                       SUM(CASE WHEN flags LIKE ? THEN 1 ELSE 0 END) AS opening_entries
                FROM entries
                WHERE user_id = ? AND created_date >= ? AND created_date < ?
                GROUP BY created_date
                """,
                (opening_like, user_id, month_start, next_month_start),
            )
            rows = await cursor.fetchall()

        for row in rows:
            created_date = row["created_date"]
            if not created_date:
                continue
            try:
                day = _date_type.fromisoformat(created_date).day
            except (TypeError, ValueError):
                continue
            total = row["total_entries"] or 0
            opening = row["opening_entries"] or 0
            if total <= 0:
                continue
            active_days.add(day)
            if opening == total:
                opening_only_days.add(day)

        return sorted(active_days), sorted(opening_only_days)

    async def get_day_summary_digests(
        self, user_id: str, year: int, month: int
    ) -> dict[int, str]:
        month_start, next_month_start = get_month_bounds(year, month)
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT created_date, digest
                FROM entries
                WHERE user_id = ? AND created_date >= ? AND created_date < ?
                """,
                (user_id, month_start, next_month_start),
            )
            rows = await cursor.fetchall()
        summary_digests: dict[int, str] = {}
        digests_by_day: dict[int, list[str]] = {}
        for row in rows:
            created_date = row["created_date"] or ""
            if not created_date:
                continue
            try:
                day = _date_type.fromisoformat(created_date).day
            except (TypeError, ValueError):
                continue
            digest = str(row["digest"] or "").strip()
            if digest:
                digests_by_day.setdefault(day, []).append(digest)
        for day, digests in digests_by_day.items():
            summary_digests[day] = day_digest(digests)
        return summary_digests

    async def get_day_summary_digest_for_date(
        self, user_id: str, created_date: str
    ) -> str:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT digest
                FROM entries
                WHERE user_id = ? AND created_date = ?
                """,
                (user_id, created_date),
            )
            rows = await cursor.fetchall()

        digests: list[str] = []
        for row in rows:
            digest = str(row["digest"] or "").strip()
            if digest:
                digests.append(digest)

        return day_digest(digests)

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
