#!/usr/bin/env python3
"""Migrate entry payloads from `message` to `text` for a specific user.

Usage::

    uv run python scripts/migrate_entry_text.py --user USER_ID:BASE64_DEK [--dry-run]

Multiple ``--user`` options may be provided. The DEK must be supplied as a
base64 encoded string for each user.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
from typing import Tuple

import orjson

from llamora.persistence.local_db import LocalDB


logger = logging.getLogger(__name__)


def _parse_user_entry(entry: str) -> Tuple[str, bytes]:
    if ":" not in entry:
        raise ValueError("Expected USER_ID:BASE64_DEK format")
    user_id, dek_b64 = entry.split(":", 1)
    if not user_id:
        raise ValueError("User identifier must not be empty")
    try:
        dek = base64.b64decode(dek_b64)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("Invalid base64 DEK") from exc
    if not dek:
        raise ValueError("Decoded DEK must not be empty")
    return user_id, dek


async def _migrate_user_entries(
    db: LocalDB,
    user_id: str,
    dek: bytes,
    *,
    dry_run: bool,
    verify_only: bool,
    sample_limit: int,
) -> Tuple[int, int]:
    repo = db.entries
    if db.pool is None:
        raise RuntimeError("Database connection pool is not initialised")

    total = 0
    updated = 0
    with_text = 0
    with_message = 0
    empty_text = 0
    sample_ids = []

    async with db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, nonce, ciphertext, alg FROM entries WHERE user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()

        for row in rows:
            total += 1
            entry_id = row["id"]
            try:
                plaintext = repo._decrypt_message(  # type: ignore[attr-defined]
                    dek,
                    user_id,
                    entry_id,
                    row["nonce"],
                    row["ciphertext"],
                    row["alg"],
                )
            except Exception:
                logger.warning(
                    "Failed to decrypt entry %s for user %s", entry_id, user_id
                )
                continue

            try:
                record = orjson.loads(plaintext)
            except Exception:
                logger.warning(
                    "Failed to parse entry %s for user %s", entry_id, user_id
                )
                continue

            if not isinstance(record, dict):
                logger.warning(
                    "Unexpected entry payload for %s (user %s)", entry_id, user_id
                )
                continue

            text_value = record.get("text")
            if text_value is not None:
                with_text += 1
                if isinstance(text_value, str) and not text_value.strip():
                    empty_text += 1
                    if len(sample_ids) < sample_limit:
                        sample_ids.append(entry_id)
                if verify_only:
                    continue
                if "message" not in record:
                    continue

            if "message" not in record:
                logger.warning(
                    "Entry %s for user %s has no message/text fields",
                    entry_id,
                    user_id,
                )
                continue

            with_message += 1
            if verify_only:
                continue

            record["text"] = record.pop("message", "")

            if dry_run:
                updated += 1
                logger.info("Would migrate entry %s for user %s", entry_id, user_id)
                continue

            nonce, ct, alg = repo._encrypt_message(  # type: ignore[attr-defined]
                dek,
                user_id,
                entry_id,
                orjson.dumps(record).decode(),
            )
            await conn.execute(
                """
                UPDATE entries
                SET nonce = ?, ciphertext = ?, alg = ?
                WHERE user_id = ? AND id = ?
                """,
                (nonce, ct, alg, user_id, entry_id),
            )
            updated += 1

        if not dry_run:
            await conn.commit()

    logger.info(
        "User %s: total=%d, has_text=%d, has_message=%d, empty_text=%d",
        user_id,
        total,
        with_text,
        with_message,
        empty_text,
    )
    if sample_ids:
        logger.info("User %s: sample empty text ids: %s", user_id, sample_ids)

    return total, updated


async def _main_async(args: argparse.Namespace) -> int:
    if not args.user:
        logger.error("At least one --user entry is required")
        return 2

    users = []
    for entry in args.user:
        try:
            users.append(_parse_user_entry(entry))
        except ValueError as exc:
            logger.error("%s", exc)
            return 2

    async with LocalDB() as db:
        for user_id, dek in users:
            total, updated = await _migrate_user_entries(
                db,
                user_id,
                dek,
                dry_run=args.dry_run,
                verify_only=args.verify_only,
                sample_limit=args.sample_limit,
            )
            logger.info(
                "User %s: %d entries scanned, %d migrated",
                user_id,
                total,
                updated,
            )

    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Migrate entry payloads from message -> text."
    )
    parser.add_argument(
        "--user",
        action="append",
        help="User and base64 DEK in the form USER_ID:BASE64_DEK",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing to the database",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only decrypt and report message/text presence without writing",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=5,
        help="Number of entry ids to sample when empty text is found",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
