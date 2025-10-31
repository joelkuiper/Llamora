#!/usr/bin/env python3
"""Retrieve a user's Data Encryption Key as a base64-encoded string."""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import sys
from pathlib import Path
from typing import TYPE_CHECKING

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if TYPE_CHECKING:  # pragma: no cover - import for type checking only
    from db import LocalDB


async def _fetch_user_dek(db: "LocalDB", username: str, password: str) -> bytes:
    try:
        from nacl import exceptions, pwhash
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise SystemExit(
            "PyNaCl is required to verify credentials. Install the project's dependencies."
        ) from exc

    from app.services.crypto import unwrap_key

    user = await db.users.get_user_by_username(username)
    if not user:
        raise SystemExit(f"User '{username}' was not found")

    hash_bytes = user["password_hash"].encode("utf-8")
    password_bytes = password.encode("utf-8")

    try:
        valid = pwhash.argon2id.verify(hash_bytes, password_bytes)
    except exceptions.InvalidkeyError:
        valid = False
    except Exception as exc:  # pragma: no cover - defensive
        raise SystemExit(f"Failed to verify password: {exc}") from exc

    if not valid:
        raise SystemExit("Invalid credentials")

    try:
        dek = unwrap_key(
            user["dek_pw_cipher"],
            user["dek_pw_salt"],
            user["dek_pw_nonce"],
            password,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise SystemExit(f"Failed to unwrap user DEK: {exc}") from exc

    if not dek:
        raise SystemExit("Retrieved DEK is empty")

    return dek


async def _run(args: argparse.Namespace) -> None:
    from db import LocalDB

    password = getpass.getpass(prompt="Password: ")
    if not password:
        raise SystemExit("Password must not be empty")

    db = LocalDB(args.db_path)
    await db.init()
    try:
        dek = await _fetch_user_dek(db, args.username, password)
    finally:
        await db.close()

    dek_b64 = base64.b64encode(dek).decode("utf-8")
    print(dek_b64)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="Username of the account to inspect")
    parser.add_argument(
        "--db-path",
        help="Path to the SQLite database (defaults to LLAMORA_DB_PATH)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
