#!/usr/bin/env python3
"""Backfill entry flags for a single user."""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging

import orjson

from llamora.app.db.entries import build_entry_flags_from_meta, parse_entry_flags
from llamora.persistence.local_db import LocalDB


logger = logging.getLogger(__name__)


async def _load_entries(db: LocalDB, user_id: str) -> list[dict]:
    assert db.pool is not None
    async with db.pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT id, nonce, ciphertext, alg, flags
            FROM entries
            WHERE user_id = ?
            """,
            (user_id,),
        )
        return await cursor.fetchall()


def _normalize_bytes(value) -> bytes:
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytes):
        return value
    if value is None:
        return b""
    return str(value).encode("utf-8")


def _decrypt_entry(db: LocalDB, dek: bytes, user_id: str, row: dict) -> dict:
    decrypt = db.entries._decrypt_message  # noqa: SLF001
    payload = decrypt(
        dek,
        user_id,
        row["id"],
        _normalize_bytes(row["nonce"]),
        _normalize_bytes(row["ciphertext"]),
        _normalize_bytes(row["alg"]),
    )
    return orjson.loads(payload)


async def _backfill_user(db: LocalDB, user_id: str, dek: bytes) -> None:
    rows = await _load_entries(db, user_id)
    if not rows:
        logger.info("No entries found for user %s", user_id)
        return

    updates: list[tuple[str, str, str]] = []
    skipped = 0
    already = 0

    for row in rows:
        entry_id = str(row["id"] or "").strip()
        if not entry_id:
            skipped += 1
            continue
        current_flags = str(row["flags"] or "")
        try:
            record = _decrypt_entry(db, dek, user_id, row)
            meta = record.get("meta", {})
            merged_flags = build_entry_flags_from_meta(
                meta if isinstance(meta, dict) else {}, parse_entry_flags(current_flags)
            )
            if merged_flags == current_flags:
                already += 1
                continue
            updates.append((merged_flags, entry_id, user_id))
        except Exception as exc:
            skipped += 1
            logger.warning("Skipping entry %s: %s", entry_id, exc)

    if not updates:
        if skipped:
            logger.error(
                "No updates applied; %d entries failed to decrypt. Check the DEK/user.",
                skipped,
            )
        else:
            logger.info("All %d entries already have flags.", already)
        return

    assert db.pool is not None
    async with db.pool.connection() as conn:
        await conn.execute("BEGIN")
        try:
            await conn.executemany(
                """
                UPDATE entries
                SET flags = ?
                WHERE id = ? AND user_id = ?
                """,
                updates,
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    logger.info(
        "Updated %d entries; %d already had flags; %d failed to decrypt",
        len(updates),
        already,
        skipped,
    )


async def _run(args: argparse.Namespace) -> None:
    db = LocalDB(args.db_path)
    await db.init()
    try:
        user = await db.users.get_user_by_username(args.username)
        if not user:
            raise SystemExit(f"User '{args.username}' was not found")
        dek = base64.b64decode(args.dek_b64)
        if not dek:
            raise SystemExit("DEK is empty")
        await _backfill_user(db, user["id"], dek)
    finally:
        await db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="Username to backfill entry flags for")
    parser.add_argument("dek_b64", help="Base64-encoded DEK for the user")
    parser.add_argument("--db-path", help="Path to the SQLite database")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
