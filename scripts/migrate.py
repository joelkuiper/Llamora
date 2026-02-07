from __future__ import annotations

import argparse
import logging
from pathlib import Path

from fastmigrate import get_db_version
from fastmigrate.core import _ensure_meta_table, _set_db_version

from llamora.settings import settings
from llamora.app.services.migrations import run_db_migrations


logger = logging.getLogger(__name__)


def _resolve_db_path(raw_path: str | None) -> Path:
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return Path(settings.DATABASE.path).expanduser().resolve()


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


async def _cmd_up(db_path: Path, verbose: bool) -> None:
    await run_db_migrations(db_path, verbose=verbose)
    version = get_db_version(db_path)
    logger.info("Database now at version %s", version)


def _cmd_status(db_path: Path) -> None:
    version = get_db_version(db_path)
    logger.info("Database version: %s", version)


def _cmd_enroll(db_path: Path, version: int) -> None:
    _ensure_meta_table(db_path)
    _set_db_version(db_path, version)
    logger.info("Enrolled database at version %s", version)


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Llamora migration helper")
    parser.add_argument("--db", dest="db_path", help="Path to sqlite database")

    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show current version")
    status_parser.add_argument("--db", dest="db_path", help="Path to sqlite database")

    up_parser = subparsers.add_parser("up", help="Apply pending migrations")
    up_parser.add_argument("--db", dest="db_path", help="Path to sqlite database")
    up_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    enroll_parser = subparsers.add_parser(
        "enroll", help="Enroll existing database at a version"
    )
    enroll_parser.add_argument("version", type=int, help="Version to enroll")
    enroll_parser.add_argument("--db", dest="db_path", help="Path to sqlite database")

    args = parser.parse_args()
    db_path = _resolve_db_path(args.db_path)

    if args.command == "status":
        _cmd_status(db_path)
        return
    if args.command == "enroll":
        _cmd_enroll(db_path, args.version)
        return

    if args.command == "up":
        import asyncio

        asyncio.run(_cmd_up(db_path, args.verbose))


if __name__ == "__main__":
    main()
