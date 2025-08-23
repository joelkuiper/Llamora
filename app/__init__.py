from quart import Quart, render_template, make_response, request
from dotenv import load_dotenv
from quart_wtf import CSRFProtect
import os
import logging
import asyncio
import secrets
from contextlib import suppress
from db import LocalDB
from app.api.search import SearchAPI

load_dotenv()

db = LocalDB()
search_api = SearchAPI(db)
db.set_search_api(search_api)


def create_app():
    app = Quart(__name__)
    app.secret_key = os.getenv("LLAMORA_SECRET_KEY")
    app.config.from_object("config")

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    CSRFProtect(app)

    from .routes.auth import auth_bp
    from .routes.days import days_bp
    from .routes.chat import chat_bp, llm
    from .routes.search import search_bp
    from .routes.tags import tags_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(days_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(tags_bp)

    import humanize
    from datetime import datetime, timezone
    import hashlib

    @app.template_filter("humanize")
    def humanize_filter(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return humanize.naturaltime(value)

    @app.template_filter("long_date")
    def long_date_filter(value):
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        else:
            dt = value
        day = dt.day
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        month = dt.strftime("%B")
        return f"{day}{suffix} of {month} {dt.year}"

    @app.template_filter("tag_hash")
    def tag_hash_filter(tag, user_id=None):
        t = tag.strip()[:64]
        uid = user_id or (getattr(request, "user", {}) or {}).get("id")
        if not uid:
            return ""
        return hashlib.sha256(f"{uid}:{t}".encode("utf-8")).hexdigest()

    from .services.auth_helpers import load_user, dek_store

    app.before_request(load_user)
    app.before_serving(db.init)
    app.after_serving(db.close)

    @app.after_serving
    async def _shutdown_llm():
        llm.shutdown()

    @app.before_serving
    async def _print_registration_link():
        if app.config.get("DISABLE_REGISTRATION"):
            if await db.users_table_empty():
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

    maintenance_task: asyncio.Task | None = None

    async def _maintenance_loop():
        try:
            while True:
                await asyncio.sleep(60)
                dek_store.expire()
                await search_api.maintenance_tick()
        except asyncio.CancelledError:
            pass

    @app.before_serving
    async def _start_maintenance():
        nonlocal maintenance_task
        maintenance_task = asyncio.create_task(_maintenance_loop())

    @app.after_serving
    async def _stop_maintenance():
        if maintenance_task:
            maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance_task

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
