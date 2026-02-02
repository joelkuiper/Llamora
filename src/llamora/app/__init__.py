from __future__ import annotations

import logging
import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from llamora.settings import settings
from .services.config_validation import validate_settings


if TYPE_CHECKING:  # pragma: no cover - import for static analysis only
    from quart import Quart


logger = logging.getLogger(__name__)


def create_app():
    from quart import Quart, render_template, make_response, request, g
    from quart import abort, send_from_directory
    from quart_wtf import CSRFProtect
    from .services.container import AppLifecycle, AppServices

    errors = validate_settings()
    if errors:
        for message in errors:
            logger.error("Configuration error: %s", message)
        raise RuntimeError("Invalid application configuration")

    services = AppServices.create()

    from .services.auth_helpers import (
        SecureCookieManager,
        SECURE_COOKIE_MANAGER_KEY,
    )

    cookie_manager = SecureCookieManager(
        cookie_name=str(settings.COOKIES.name),
        cookie_secret=str(settings.COOKIES.secret or ""),
        dek_storage=str(settings.CRYPTO.dek_storage),
        session_ttl=int(settings.SESSION.ttl),
    )

    lifecycle = AppLifecycle(services, cookie_manager.dek_store)

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

    module_path = Path(__file__).resolve()
    project_root = module_path.parents[2]
    static_fallback_dir = project_root / "frontend" / "static"
    if not static_fallback_dir.exists():
        project_root = module_path.parents[3]
        static_fallback_dir = project_root / "frontend" / "static"

    dist_dir = project_root / "frontend" / "dist"
    manifest_path = dist_dir / "manifest.json"
    asset_manifest: dict[str, dict[str, str]] = {"js": {}, "css": {}}
    static_bundles = False

    if manifest_path.exists():
        try:
            asset_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Failed to parse asset manifest at %s", manifest_path)
        else:
            static_bundles = True

    app = Quart(__name__, static_folder=None, static_url_path="/static")
    app.secret_key = settings.SECRET_KEY
    app.config.update(
        APP_NAME=settings.APP_NAME,
        DISABLE_REGISTRATION=bool(settings.FEATURES.disable_registration),
        MAX_USERNAME_LENGTH=int(settings.LIMITS.max_username_length),
        MAX_PASSWORD_LENGTH=int(settings.LIMITS.max_password_length),
        MIN_PASSWORD_LENGTH=int(settings.LIMITS.min_password_length),
        MAX_MESSAGE_LENGTH=int(settings.LIMITS.max_message_length),
        MAX_TAG_LENGTH=int(settings.LIMITS.max_tag_length),
        MAX_SEARCH_QUERY_LENGTH=int(settings.LIMITS.max_search_query_length),
        ALLOWED_LLM_CONFIG_KEYS=set(settings.LLM.allowed_config_keys),
        SESSION_TTL=int(settings.SESSION.ttl),
        PERMANENT_SESSION_LIFETIME=settings.SESSION.permanent_lifetime,
        WTF_CSRF_TIME_LIMIT=settings.SESSION.csrf_time_limit,
        EMBED_MODEL=settings.EMBEDDING.model,
        STATIC_BUNDLES=static_bundles,
        STATIC_MANIFEST=asset_manifest,
        STATIC_DIST_PATH=str(dist_dir),
        STATIC_FALLBACK_PATH=str(static_fallback_dir),
    )

    app.extensions["llamora"] = services
    app.extensions[SECURE_COOKIE_MANAGER_KEY] = cookie_manager

    @app.route("/static/<path:filename>", endpoint="static")
    async def static_file(filename: str):
        dist_candidate = dist_dir / filename
        if dist_candidate.exists():
            return await send_from_directory(str(dist_dir), filename)

        fallback_candidate = static_fallback_dir / filename
        if fallback_candidate.exists():
            return await send_from_directory(str(static_fallback_dir), filename)

        abort(404)

    logging.basicConfig(
        level=settings.LOG_LEVEL,
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
