"""Entry point for running Llamora as a module."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from logging import getLogger
from typing import Sequence

from hypercorn.asyncio import serve
from hypercorn.config import Config

from llamora.settings import settings

from . import create_app
from .app.services.migrations import run_db_migrations

logger = getLogger(__name__)

app = create_app()


def _resolve_host_port(
    override_host: str | None, override_port: int | None
) -> tuple[str, int]:
    host_setting = settings.get("APP.host")
    port_setting = settings.get("APP.port")

    host = override_host or host_setting or "127.0.0.1"
    port_value: int | str | None = (
        override_port if override_port is not None else port_setting
    )
    port = int(port_value) if port_value is not None else 5000
    return host, port


async def _run_prod(
    host: str,
    port: int,
    *,
    workers: int | None,
    keep_alive: float | None,
    graceful_timeout: float | None,
) -> None:
    # Ensure the database exists and all migrations are applied before serving.
    db_path = Path(str(settings.DATABASE.path)).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Applying database migrations at %s", db_path)
    await run_db_migrations(db_path, verbose=False)
    config = Config()
    config.bind = [f"{host}:{port}"]

    if workers is not None:
        config.workers = workers
    if keep_alive is not None:
        config.keep_alive_timeout = keep_alive
    if graceful_timeout is not None:
        config.graceful_timeout = graceful_timeout

    logger.info(
        "Starting Hypercorn on %s:%s (workers=%s, keep_alive=%s, graceful_timeout=%s)",
        host,
        port,
        config.workers,
        config.keep_alive_timeout,
        config.graceful_timeout,
    )

    await serve(app, config)


def _run_dev(host: str, port: int, *, reload: bool) -> None:
    # Ensure the database exists and all migrations are applied before serving.
    db_path = Path(str(settings.DATABASE.path)).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Applying database migrations at %s", db_path)
    asyncio.run(run_db_migrations(db_path, verbose=False))

    logger.info(
        "Starting Quart development server on %s:%s (reload=%s)", host, port, reload
    )
    app.run(host=host, port=port, use_reloader=reload)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Llamora application server.")
    parser.add_argument(
        "--host", help="Override the bind host (defaults to settings.APP.host)"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Override the bind port (defaults to settings.APP.port)",
    )

    subparsers = parser.add_subparsers(dest="mode")

    dev_parser = subparsers.add_parser(
        "dev", help="Run the Quart development server with reload support."
    )
    dev_parser.set_defaults(mode="dev")
    dev_parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable the code reloader (enabled by default).",
    )

    prod_parser = subparsers.add_parser(
        "prod", help="Run the Hypercorn production server."
    )
    prod_parser.set_defaults(mode="prod")
    prod_parser.add_argument(
        "--workers",
        type=int,
        help="Number of Hypercorn worker processes (defaults to Hypercorn's auto-detection).",
    )
    prod_parser.add_argument(
        "--keep-alive",
        type=float,
        help="Keep-alive timeout in seconds (defaults to Hypercorn's configuration).",
    )
    prod_parser.add_argument(
        "--graceful-timeout",
        type=float,
        help="Graceful shutdown timeout in seconds (defaults to Hypercorn's configuration).",
    )

    parser.set_defaults(mode="prod")

    return parser


def cli(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    host, port = _resolve_host_port(args.host, args.port)

    if args.mode == "dev":
        reload_enabled = not getattr(args, "no_reload", False)
        _run_dev(host, port, reload=reload_enabled)
        return

    if args.mode == "prod":
        asyncio.run(
            _run_prod(
                host,
                port,
                workers=getattr(args, "workers", None),
                keep_alive=getattr(args, "keep_alive", None),
                graceful_timeout=getattr(args, "graceful_timeout", None),
            )
        )
        return

    parser.error("No run mode selected. Use 'dev' or 'prod'.")


def main() -> None:
    cli()


def prod() -> None:
    cli(["prod", *sys.argv[1:]])


def dev() -> None:
    cli(["dev", *sys.argv[1:]])


if __name__ == "__main__":
    main()
