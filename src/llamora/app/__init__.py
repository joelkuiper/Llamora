from quart import Quart, render_template, make_response, request, g
from dotenv import load_dotenv
from quart_wtf import CSRFProtect
import os
import logging
import secrets

load_dotenv()


def create_app():
    from .services.container import AppLifecycle, AppServices

    services = AppServices.create()

    from .services.auth_helpers import dek_store

    lifecycle = AppLifecycle(services, dek_store)

    async def _ensure_registration_token(app: Quart) -> None:
        if not app.config.get("DISABLE_REGISTRATION"):
            app.config["REGISTRATION_TOKEN"] = None
            return

        if await services.db.users.users_table_empty():
            token = secrets.token_urlsafe(32)
            app.config["REGISTRATION_TOKEN"] = token
            server = app.config.get("SERVER_NAME")
            scheme = app.config.get("PREFERRED_URL_SCHEME", "http")
            if server:
                url = f"{scheme}://{server}/register?token={token}"
            else:
                url = f"/register?token={token}"
            app.logger.warning(
                "Registration disabled but no users exist. One-time URL: %s",
                url,
            )
        else:
            app.config["REGISTRATION_TOKEN"] = None

    app = Quart(__name__)
    app.secret_key = os.getenv("LLAMORA_SECRET_KEY")
    app.config.from_object("llamora.config")

    app.extensions["llamora"] = services

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    CSRFProtect(app)

    from .routes.auth import auth_bp
    from .routes.days import days_bp
    from .routes.chat import chat_bp
    from .routes.search import search_bp
    from .routes.tags import tags_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(days_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(tags_bp)

    from datetime import datetime
    from .util.tags import canonicalize as canonicalize_tag, display as display_tag
    from .util.tags import tag_hash as compute_tag_hash
    from .services.time import (
        humanize as humanize_filter,
        format_date,
    )

    app.template_filter("humanize")(humanize_filter)

    @app.template_filter("long_date")
    def long_date_filter(value):
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        else:
            dt = value
        return format_date(dt)

    app.jinja_env.globals["display"] = display_tag

    @app.template_filter("tag_hash")
    def tag_hash_filter(tag, user_id=None):
        current_user = getattr(g, "_current_user", None) or {}
        uid = user_id or current_user.get("id")
        if not uid:
            return ""
        try:
            canonical = canonicalize_tag(tag)
        except ValueError:
            return ""
        return compute_tag_hash(uid, canonical).hex()

    from .services.auth_helpers import load_user

    app.before_request(load_user)
    app.extensions["llamora_lifecycle"] = lifecycle

    def _install_lifecycle() -> None:
        if hasattr(app, "lifecycle"):

            @app.lifecycle  # type: ignore[misc]
            async def _lifespan(app: Quart):
                async with lifecycle:
                    await _ensure_registration_token(app)
                    yield

        else:

            @app.before_serving
            async def _start_lifecycle() -> None:
                await lifecycle.start()
                await _ensure_registration_token(app)

            @app.after_serving
            async def _stop_lifecycle() -> None:
                await lifecycle.stop()

    _install_lifecycle()

    @app.errorhandler(404)
    async def not_found(e):
        message = getattr(e, "description", "Page not found.")
        if request.headers.get("HX-Request"):
            html = await render_template("partials/error.html", message=message)
            return await make_response(html, 404)
        html = await render_template("error.html", message=message)
        return await make_response(html, 404)

    @app.errorhandler(Exception)
    async def handle_exception(e):
        app.logger.exception("Unhandled exception: %s", e)
        message = "An unexpected error occurred. Please try again later."
        if request.headers.get("HX-Request"):
            html = await render_template("partials/error.html", message=message)
            return await make_response(html, 500)
        html = await render_template("error.html", message=message)
        return await make_response(html, 500)

    app.logger.info("Application initialized")
    return app
