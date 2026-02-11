#!/usr/bin/env python3
"""Migrate tags to kebab-case canonicalization for a single user."""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from llamora.app.util.tags import canonicalize, tag_hash
from llamora.persistence.local_db import LocalDB


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _TagRow:
    old_hash: bytes
    canonical: str
    seen: int
    last_seen: str | None


def _parse_last_seen(value: str | None) -> str | None:
    if not value:
        return None
    return str(value)


async def _load_tags(db: LocalDB, user_id: str) -> list[dict]:
    assert db.pool is not None
    async with db.pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT tag_hash, name_ct, name_nonce, alg, seen, last_seen
            FROM tags
            WHERE user_id = ?
            """,
            (user_id,),
        )
        return await cursor.fetchall()


def _decrypt_tag_name(db: LocalDB, dek: bytes, user_id: str, row: dict) -> str:
    tag_hash = bytes(row["tag_hash"])
    name_ct = bytes(row["name_ct"])
    name_nonce = bytes(row["name_nonce"])
    alg = str(row["alg"] or "").encode("utf-8")
    decrypt = db.tags._decrypt_message  # noqa: SLF001
    return decrypt(dek, user_id, tag_hash.hex(), name_nonce, name_ct, alg)


async def _upsert_tag(
    conn,
    db: LocalDB,
    user_id: str,
    dek: bytes,
    new_hash: bytes,
    canonical: str,
    seen: int,
    last_seen: str | None,
) -> None:
    encrypt = db.tags._encrypt_message  # noqa: SLF001
    nonce, ct, alg = encrypt(dek, user_id, new_hash.hex(), canonical)
    await conn.execute(
        """
        INSERT OR IGNORE INTO tags (user_id, tag_hash, name_ct, name_nonce, alg, seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, new_hash, ct, nonce, alg.decode(), seen, last_seen),
    )
    await conn.execute(
        """
        UPDATE tags
        SET name_ct = ?, name_nonce = ?, alg = ?, seen = ?, last_seen = ?
        WHERE user_id = ? AND tag_hash = ?
        """,
        (ct, nonce, alg.decode(), seen, last_seen, user_id, new_hash),
    )


async def _migrate_user_tags(db: LocalDB, user_id: str, dek: bytes) -> None:
    rows = await _load_tags(db, user_id)
    if not rows:
        logger.info("No tags found for user %s", user_id)
        return

    grouped: dict[bytes, list[_TagRow]] = defaultdict(list)
    skipped = 0

    for row in rows:
        try:
            plaintext = _decrypt_tag_name(db, dek, user_id, row)
            canonical = canonicalize(plaintext)
        except Exception as exc:
            skipped += 1
            logger.warning("Skipping tag (decrypt/canonicalize failed): %s", exc)
            continue
        new_hash = tag_hash(user_id, canonical)
        grouped[new_hash].append(
            _TagRow(
                old_hash=bytes(row["tag_hash"]),
                canonical=canonical,
                seen=int(row["seen"] or 0),
                last_seen=_parse_last_seen(row["last_seen"]),
            )
        )

    if skipped:
        logger.info("Skipped %d tags due to decode/canonicalize failures", skipped)

    assert db.pool is not None
    async with db.pool.connection() as conn:
        await conn.execute("BEGIN")
        try:
            for new_hash, items in grouped.items():
                canonical = items[0].canonical
                last_seen_values = [i.last_seen for i in items if i.last_seen]
                last_seen = max(last_seen_values) if last_seen_values else None

                # Ensure the target tag row exists before moving xrefs (FK safety).
                await _upsert_tag(
                    conn, db, user_id, dek, new_hash, canonical, 0, last_seen
                )

                # Move xrefs for all old hashes to the new hash.
                for item in items:
                    if item.old_hash == new_hash:
                        continue
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO tag_entry_xref (user_id, tag_hash, entry_id, ulid)
                        SELECT x.user_id, ?, x.entry_id, x.ulid
                        FROM tag_entry_xref x
                        JOIN entries e
                          ON e.user_id = x.user_id AND e.id = x.entry_id
                        WHERE x.user_id = ? AND x.tag_hash = ?
                        """,
                        (new_hash, user_id, item.old_hash),
                    )
                    await conn.execute(
                        "DELETE FROM tag_entry_xref WHERE user_id = ? AND tag_hash = ?",
                        (user_id, item.old_hash),
                    )

                # Recompute seen from xrefs for accuracy.
                cursor = await conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM tag_entry_xref
                    WHERE user_id = ? AND tag_hash = ?
                    """,
                    (user_id, new_hash),
                )
                row = await cursor.fetchone()
                seen = int(row["cnt"] or 0) if row else 0

                await _upsert_tag(
                    conn, db, user_id, dek, new_hash, canonical, seen, last_seen
                )

                for item in items:
                    if item.old_hash == new_hash:
                        continue
                    await conn.execute(
                        "DELETE FROM tags WHERE user_id = ? AND tag_hash = ?",
                        (user_id, item.old_hash),
                    )

            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    logger.info("Tag migration complete for user %s", user_id)


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
        await _migrate_user_tags(db, user["id"], dek)
    finally:
        await db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="Username to migrate tags for")
    parser.add_argument("dek_b64", help="Base64-encoded DEK for the user")
    parser.add_argument("--db-path", help="Path to the SQLite database")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
