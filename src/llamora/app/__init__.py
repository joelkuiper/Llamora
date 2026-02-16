from __future__ import annotations

import logging
import json
import secrets
import os
import re
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
    from typing import Any, cast
    from .services.container import AppLifecycle, AppServices

    errors = validate_settings()
    if errors:
        for message in errors:
            logger.error("Configuration error: %s", message)
        raise RuntimeError("Invalid application configuration")

    services = AppServices.create()

    from .util.number import parse_positive_int

    def _detect_worker_count() -> int:
        env_keys = (
            "WEB_CONCURRENCY",
            "HYPERCORN_WORKERS",
            "UVICORN_WORKERS",
            "GUNICORN_WORKERS",
        )
        for key in env_keys:
            parsed = parse_positive_int(os.getenv(key))
            if parsed:
                return parsed
        cmd_args = os.getenv("GUNICORN_CMD_ARGS", "")
        if cmd_args:
            match = re.search(r"(?:--workers|-w)\\s+(\\d+)", cmd_args)
            if match:
                parsed = parse_positive_int(match.group(1))
                if parsed:
                    return parsed
        parsed = parse_positive_int(settings.get("APP.workers"))
        return parsed or 1

    worker_count = _detect_worker_count()
    dek_storage = str(settings.CRYPTO.dek_storage or "cookie").lower()
    if worker_count > 1 and dek_storage == "session":
        logger.warning(
            "Multi-worker (%d) detected; forcing CRYPTO.dek_storage=cookie",
            worker_count,
        )
        dek_storage = "cookie"

    from .services.auth_helpers import (
        SecureCookieManager,
        SECURE_COOKIE_MANAGER_KEY,
    )

    cookie_manager = SecureCookieManager(
        cookie_name=str(settings.COOKIES.name),
        cookie_secret=str(settings.COOKIES.secret or ""),
        dek_storage=dek_storage,
        force_secure=bool(settings.COOKIES.force_secure),
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
    manifest_mtime: float | None = None

    def _load_manifest() -> tuple[dict[str, dict[str, str]], bool, float | None]:
        if not manifest_path.exists():
            return {"js": {}, "css": {}}, False, None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Failed to parse asset manifest at %s", manifest_path)
            return {"js": {}, "css": {}}, False, None
        return manifest, True, manifest_path.stat().st_mtime

    asset_manifest, static_bundles, manifest_mtime = _load_manifest()

    app = Quart(__name__, static_folder=None, static_url_path="/static")
    app.secret_key = settings.SECRET_KEY
    debug_flag = bool(getattr(settings, "DEBUG", False)) or os.getenv(
        "QUART_DEBUG"
    ) in (
        "1",
        "true",
        "True",
    )
    proxy_hops = parse_positive_int(settings.get("PROXY.trusted_hops")) or 0
    if proxy_hops > 0:
        try:
            from quart.middleware.proxy_fix import ProxyFix  # type: ignore
        except Exception:  # pragma: no cover - fallback for older Quart
            from werkzeug.middleware.proxy_fix import ProxyFix  # type: ignore
        app.asgi_app = cast(
            Any,
            ProxyFix(
                cast(Any, app.asgi_app),
                x_for=proxy_hops,
                x_proto=proxy_hops,
                x_host=proxy_hops,
                x_port=proxy_hops,
                x_prefix=proxy_hops,
            ),
        )
        logger.info("ProxyFix enabled with trusted_hops=%d", proxy_hops)

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
        STATIC_MANIFEST_MTIME=manifest_mtime,
        DEBUG=debug_flag,
        TEMPLATES_AUTO_RELOAD=debug_flag,
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
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
    app.jinja_env.keep_trailing_newline = False
    app.jinja_env.auto_reload = debug_flag
    if debug_flag:

        @app.before_request
        async def _clear_template_cache() -> None:
            if app.jinja_env.cache is not None:
                app.jinja_env.cache.clear()

    from .routes.auth import auth_bp
    from .routes.days import days_bp
    from .routes.entries import entries_bp
    from .routes.entries_stream import entries_stream_bp
    from .routes.search import search_bp
    from .routes.tags import tags_bp
    from .api.lockbox_api import lockbox_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(days_bp)
    app.register_blueprint(entries_bp)
    app.register_blueprint(entries_stream_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(tags_bp)
    app.register_blueprint(lockbox_bp)

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

    async def _refresh_manifest() -> None:
        if not app.config.get("DEBUG"):
            return
        if not manifest_path.exists():
            if app.config.get("STATIC_BUNDLES"):
                app.config["STATIC_BUNDLES"] = False
                app.config["STATIC_MANIFEST"] = {"js": {}, "css": {}}
                app.config["STATIC_MANIFEST_MTIME"] = None
            return
        last_mtime = app.config.get("STATIC_MANIFEST_MTIME")
        current_mtime = manifest_path.stat().st_mtime
        if last_mtime is None or current_mtime > last_mtime:
            manifest, bundles, mtime = _load_manifest()
            app.config["STATIC_MANIFEST"] = manifest
            app.config["STATIC_BUNDLES"] = bundles
            app.config["STATIC_MANIFEST_MTIME"] = mtime

    app.before_request(load_user)
    app.before_request(_refresh_manifest)
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
            html = await render_template(
                "components/errors/error.html", message=message
            )
            return await make_response(html, 404)
        html = await render_template("pages/error.html", message=message)
        return await make_response(html, 404)

    @app.errorhandler(Exception)
    async def handle_exception(e):
        app.logger.exception("Unhandled exception: %s", e)
        message = "An unexpected error occurred. Please try again later."
        if request.headers.get("HX-Request"):
            html = await render_template(
                "components/errors/error.html", message=message
            )
            return await make_response(html, 500)
        html = await render_template("pages/error.html", message=message)
        return await make_response(html, 500)

    app.logger.info("Application initialized")
    return app
