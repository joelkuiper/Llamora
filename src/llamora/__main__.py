"""Entry point for running Llamora as a module."""

from __future__ import annotations

from llamora.settings import settings

from . import create_app

app = create_app()


def main() -> None:
    """Run the Quart development server with configuration overrides."""

    host = settings.get("APP.host")
    raw_port = settings.get("APP.port")
    port = int(raw_port) if raw_port is not None else 5000
    app.run(host=host or "127.0.0.1", port=port)


if __name__ == "__main__":
    main()
