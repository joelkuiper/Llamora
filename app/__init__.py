from quart import Quart, render_template
from dotenv import load_dotenv
from quart_wtf import CSRFProtect
import os
import logging
from db import LocalDB

load_dotenv()

db = LocalDB()


def create_app():
    app = Quart(__name__)
    app.secret_key = os.getenv("CHAT_SECRET_KEY")
    app.config.from_object("config")

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    csrf = CSRFProtect(app)

    from .routes.auth import auth_bp
    from .routes.chat import chat_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)

    from .services.auth_helpers import load_user

    app.before_request(load_user)

    @app.errorhandler(404)
    async def not_found(_):
        return (
            await render_template("partials/error.html", message="Page not found."),
            404,
        )

    @app.errorhandler(Exception)
    async def handle_exception(e):
        app.logger.exception("Unhandled exception: %s", e)
        return (
            await render_template(
                "partials/error.html",
                message="An unexpected error occurred. Please try again later.",
            ),
            500,
        )

    app.logger.info("Application initialized")
    return app
