#!/usr/bin/env python3
"""One-off helper to rewrite stored tag names without leading '#'.

The application now stores tag names in their canonical form (without the
leading ``#``). Existing installations may still have encrypted tag payloads
that include the prefix. This script can be used to normalise those entries by
decrypting each tag name, removing the legacy prefix, and re-encrypting the
canonical value.

Usage::

    python scripts/normalize_tags.py --user USER_ID:BASE64_DEK [--dry-run]

Multiple ``--user`` options may be provided. The DEK must be supplied as a
base64 encoded string for each user that should be processed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
from pathlib import Path
from typing import Dict, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

if str(REPO_ROOT) not in sys.path:
    # Ensure the repository root is importable when executing the script
    sys.path.insert(0, str(REPO_ROOT))

from db import LocalDB
from app.util.tags import canonicalize


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


async def _normalise_user_tags(
    db: LocalDB, user_id: str, dek: bytes, *, dry_run: bool
) -> Tuple[int, int]:
    repo = db.tags
    if db.pool is None:
        raise RuntimeError("Database connection pool is not initialised")

    total = 0
    updated = 0
    async with db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT tag_hash, name_ct, name_nonce, alg FROM tags WHERE user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            total += 1
            tag_hash = row["tag_hash"]
            alg = (row["alg"] or "").encode()
            try:
                plaintext = repo._decrypt_message(  # type: ignore[attr-defined]
                    dek,
                    user_id,
                    tag_hash.hex(),
                    row["name_nonce"],
                    row["name_ct"],
                    alg,
                )
            except Exception:
                logger.warning(
                    "Failed to decrypt tag %s for user %s", tag_hash.hex(), user_id
                )
                continue

            if not plaintext or not plaintext.startswith("#"):
                continue

            try:
                canonical = canonicalize(plaintext)
            except ValueError:
                logger.debug(
                    "Skipping tag %s for user %s due to empty canonical value",
                    tag_hash.hex(),
                    user_id,
                )
                continue

            if dry_run:
                updated += 1
                logger.info(
                    "Would normalise tag %s for user %s", tag_hash.hex(), user_id
                )
                continue

            nonce, ct, alg_bytes = repo._encrypt_message(  # type: ignore[attr-defined]
                dek,
                user_id,
                tag_hash.hex(),
                canonical,
            )
            await conn.execute(
                """
                UPDATE tags
                SET name_ct = ?, name_nonce = ?, alg = ?
                WHERE user_id = ? AND tag_hash = ?
                """,
                (ct, nonce, alg_bytes.decode(), user_id, tag_hash),
            )
            updated += 1

        if not dry_run:
            await conn.commit()

    return total, updated


async def _run(args: argparse.Namespace) -> None:
    users: Dict[str, bytes] = {}
    for entry in args.user or []:
        user_id, dek = _parse_user_entry(entry)
        users[user_id] = dek

    if not users:
        raise SystemExit("At least one --user USER_ID:BASE64_DEK argument is required")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = LocalDB(args.db_path)
    await db.init()
    try:
        grand_total = 0
        grand_updated = 0
        for user_id, dek in users.items():
            total, updated = await _normalise_user_tags(
                db, user_id, dek, dry_run=args.dry_run
            )
            logger.info(
                "%s tags inspected for user %s; %s %s",
                total,
                user_id,
                updated,
                "would be updated" if args.dry_run else "updated",
            )
            grand_total += total
            grand_updated += updated

        logger.info(
            "Completed tag normalisation: %s inspected, %s %s",
            grand_total,
            grand_updated,
            "would be updated" if args.dry_run else "updated",
        )
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user",
        action="append",
        help="Pair in the form USER_ID:BASE64_DEK identifying a user to normalise",
    )
    parser.add_argument(
        "--db-path",
        help="Path to the SQLite database (defaults to LLAMORA_DB_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report tags that would be changed without modifying the database",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
