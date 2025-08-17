from quart import (
    Quart,
    render_template,
    make_response,
    request,
    redirect,
    url_for,
    Response,
)
from dotenv import load_dotenv
from quart_wtf import CSRFProtect
from quart_wtf.csrf import CSRFError
import os
import logging
import asyncio
from contextlib import suppress
from db import LocalDB
from app.api.search import SearchAPI
from datetime import timedelta

load_dotenv()

db = LocalDB()
search_api = SearchAPI(db)
db.set_search_api(search_api)


def create_app():
    app = Quart(__name__)
    app.secret_key = os.getenv("LLAMORA_SECRET_KEY")
    app.config.from_object("config")
    app.permanent_session_lifetime = timedelta(
        seconds=app.config["SESSION_TTL_SECONDS"]
    )

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    CSRFProtect(app)

    from .routes.auth import auth_bp
    from .routes.sessions import sessions_bp
    from .routes.chat import chat_bp
    from .routes.search import search_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(search_bp)

    from .services.auth_helpers import load_user, clear_secure_cookie
    from .services import session_store

    app.before_request(load_user)
    app.before_serving(db.init)
    app.after_serving(db.close)

    maintenance_task: asyncio.Task | None = None

    async def _maintenance_loop():
        try:
            while True:
                await asyncio.sleep(60)
                await search_api.maintenance_tick()
                session_store.cleanup_expired()
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

    @app.errorhandler(CSRFError)
    async def handle_csrf_error(e):
        message = e.description or "CSRF token missing or invalid."
        login_url = url_for("auth.login")
        if "expired" in message.lower():
            if request.headers.get("HX-Request"):
                resp = Response(status=401)
                resp.headers["HX-Redirect"] = login_url
                clear_secure_cookie(resp)
                return resp
            resp = redirect(login_url)
            clear_secure_cookie(resp)
            return resp
        if request.headers.get("HX-Request"):
            html = await render_template("partials/error.html", message=message)
            return await make_response(html, 400)
        html = await render_template("error.html", message=message)
        return await make_response(html, 400)

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
