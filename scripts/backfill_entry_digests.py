#!/usr/bin/env python3
"""Backfill entry digests for a single user."""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging

import orjson

from llamora.app.services.crypto import entry_digest
from llamora.persistence.local_db import LocalDB


logger = logging.getLogger(__name__)

_ENTRY_DIGEST_VERSION = 2


async def _load_entries(db: LocalDB, user_id: str) -> list[dict]:
    assert db.pool is not None
    async with db.pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT id, role, nonce, ciphertext, alg, digest, digest_version
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


async def _backfill_user(
    db: LocalDB, user_id: str, dek: bytes, *, force: bool = False
) -> None:
    rows = await _load_entries(db, user_id)
    if not rows:
        logger.info("No entries found for user %s", user_id)
        return

    updates: list[tuple[str, int, str, str]] = []
    skipped = 0
    already = 0

    for row in rows:
        if row["digest"] and not force:
            already += 1
            continue
        entry_id = str(row["id"] or "").strip()
        role = str(row["role"] or "").strip()
        if not entry_id or not role:
            skipped += 1
            continue
        try:
            record = _decrypt_entry(db, dek, user_id, row)
            text = str(record.get("text") or "")
            digest = entry_digest(dek, entry_id, role, text)
            updates.append((digest, _ENTRY_DIGEST_VERSION, entry_id, user_id))
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
            logger.info("All %d entries already have digests.", already)
        return

    assert db.pool is not None
    async with db.pool.connection() as conn:
        await conn.execute("BEGIN")
        try:
            await conn.executemany(
                """
                UPDATE entries
                SET digest = ?,
                    digest_version = ?
                WHERE id = ? AND user_id = ?
                """,
                updates,
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    logger.info(
        "Updated %d entries; %d already had digests; %d failed to decrypt",
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
        await _backfill_user(db, user["id"], dek, force=args.force)
    finally:
        await db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="Username to backfill entry digests for")
    parser.add_argument("dek_b64", help="Base64-encoded DEK for the user")
    parser.add_argument("--db-path", help="Path to the SQLite database")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute digests even for entries that already have one",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
