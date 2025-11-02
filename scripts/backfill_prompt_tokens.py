"""Add or backfill ``prompt_tokens`` counts for encrypted chat messages."""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
from typing import Dict, Sequence, Tuple

import orjson

from llamora.llm.tokenizers.tokenizer import count_message_tokens


logger = logging.getLogger(__name__)


def _parse_user(entry: str) -> Tuple[str, bytes]:
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


async def _ensure_column(db) -> None:
    if db.pool is None:
        raise RuntimeError("Database connection pool is not initialised")

    async with db.pool.connection() as conn:
        cursor = await conn.execute("PRAGMA table_info(messages)")
        rows = await cursor.fetchall()
        column_names = {row["name"] for row in rows}
        if "prompt_tokens" in column_names:
            return

        logger.info("Adding prompt_tokens column to messages table")
        await conn.execute(
            "ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER DEFAULT 0"
        )
        await conn.commit()


async def _backfill_user(
    db,
    user_id: str,
    dek: bytes,
    *,
    batch_size: int,
    dry_run: bool,
    force: bool,
) -> Tuple[int, int]:
    repo = db.messages
    if repo is None:
        raise RuntimeError("Messages repository is not initialised")
    if db.pool is None:
        raise RuntimeError("Database connection pool is not initialised")

    total = 0
    updated = 0
    pending_updates: list[Tuple[int, str, str]] = []

    async with db.pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT id, role, nonce, ciphertext, alg, prompt_tokens
            FROM messages
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()

        for row in rows:
            total += 1
            current_tokens = row["prompt_tokens"] or 0
            if current_tokens > 0 and not force:
                continue

            try:
                plaintext = repo._decrypt_message(  # type: ignore[attr-defined]
                    dek,
                    user_id,
                    row["id"],
                    row["nonce"],
                    row["ciphertext"],
                    row["alg"],
                )
            except Exception:
                logger.warning(
                    "Failed to decrypt message %s for user %s",
                    row["id"],
                    user_id,
                )
                continue

            try:
                payload = orjson.loads(plaintext)
            except Exception:
                logger.warning(
                    "Invalid message payload for %s (user %s)", row["id"], user_id
                )
                continue

            message = ""
            if isinstance(payload, dict):
                raw_message = payload.get("message")
                if isinstance(raw_message, str):
                    message = raw_message
                else:
                    message = "" if raw_message is None else str(raw_message)
            else:
                logger.debug(
                    "Unexpected payload type %s for message %s",
                    type(payload),
                    row["id"],
                )

            tokens = await asyncio.to_thread(count_message_tokens, row["role"], message)

            if dry_run:
                updated += 1
                continue

            pending_updates.append((tokens, row["id"], user_id))
            updated += 1

            if len(pending_updates) >= batch_size:
                await conn.executemany(
                    """
                    UPDATE messages
                    SET prompt_tokens = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    pending_updates,
                )
                await conn.commit()
                pending_updates.clear()

        if pending_updates and not dry_run:
            await conn.executemany(
                """
                UPDATE messages
                SET prompt_tokens = ?
                WHERE id = ? AND user_id = ?
                """,
                pending_updates,
            )
            await conn.commit()

    return total, updated


async def _run(args: argparse.Namespace) -> None:
    from llamora.persistence.local_db import LocalDB

    users: Dict[str, bytes] = {}
    for entry in args.user or []:
        user_id, dek = _parse_user(entry)
        users[user_id] = dek

    if not users:
        raise SystemExit("At least one --user USER_ID:BASE64_DEK argument is required")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = LocalDB(args.db_path)
    await db.init()
    try:
        await _ensure_column(db)

        grand_total = 0
        grand_updated = 0
        for user_id, dek in users.items():
            total, updated = await _backfill_user(
                db,
                user_id,
                dek,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                force=args.force,
            )
            logger.info(
                "%s messages inspected for user %s; %s %s",
                total,
                user_id,
                updated,
                "would be updated" if args.dry_run else "updated",
            )
            grand_total += total
            grand_updated += updated

        logger.info(
            "Completed prompt token backfill: %s inspected, %s %s",
            grand_total,
            grand_updated,
            "would be updated" if args.dry_run else "updated",
        )
    finally:
        await db.close()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise argparse.ArgumentTypeError("Expected a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than zero")
    return parsed


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user",
        action="append",
        help=(
            "User specification in USER_ID:BASE64_DEK format. "
            "Provide once per user to process."
        ),
    )
    parser.add_argument(
        "--db-path",
        help="Path to the SQLite database (defaults to LLAMORA_DB_PATH)",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=200,
        help="Number of updates to batch per transaction (default: 200)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which rows would be updated without modifying the database",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute counts even when prompt_tokens already contains a value",
    )

    args = parser.parse_args(argv)
    asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
