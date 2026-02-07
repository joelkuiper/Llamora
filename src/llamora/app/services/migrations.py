from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from fastmigrate import create_db, get_db_version, run_migrations
from fastmigrate.core import _ensure_meta_table, _set_db_version

from llamora.settings import settings
from llamora.util import resolve_data_path


logger = logging.getLogger(__name__)


def _resolve_migrations_dir() -> Path:
    configured = getattr(settings, "MIGRATIONS", {}).get("path", "migrations")
    candidate = Path(str(configured))
    repo_root = Path(__file__).resolve().parents[3]

    if candidate.is_absolute():
        return candidate

    cwd_path = (Path.cwd() / candidate).resolve()
    if cwd_path.exists():
        return cwd_path

    repo_path = (repo_root / candidate).resolve()
    if repo_path.exists():
        return repo_path

    return resolve_data_path(
        str(candidate),
        fallback_dir=repo_root / "migrations",
    )


def _db_has_tables(db_path: Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
            LIMIT 1
            """
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def _enroll_db(db_path: Path, version: int) -> None:
    _ensure_meta_table(db_path)
    _set_db_version(db_path, version)


def _run_migrations_sync(db_path: Path, *, verbose: bool) -> None:
    migrations_dir = _resolve_migrations_dir()
    if not migrations_dir.exists():
        raise FileNotFoundError(f"Migrations directory not found: {migrations_dir}")

    baseline_version = int(
        getattr(settings, "MIGRATIONS", {}).get("baseline_version", 1)
    )

    if not db_path.exists():
        create_db(db_path)
    else:
        try:
            get_db_version(db_path)
        except sqlite3.Error:
            if _db_has_tables(db_path):
                logger.warning(
                    "Enrolling existing database at version %s: %s",
                    baseline_version,
                    db_path,
                )
                _enroll_db(db_path, baseline_version)
            else:
                create_db(db_path)

    ok = run_migrations(db_path, migrations_dir, verbose=verbose)
    if not ok:
        raise RuntimeError(f"Database migrations failed for {db_path}")


async def run_db_migrations(db_path: Path, *, verbose: bool = False) -> None:
    await asyncio.to_thread(_run_migrations_sync, db_path, verbose=verbose)
