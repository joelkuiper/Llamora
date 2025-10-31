"""Entry point for running Llamora as a module."""

from . import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
