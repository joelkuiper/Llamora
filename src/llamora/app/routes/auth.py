import asyncio

from quart import (
    Blueprint,
    render_template,
    request,
    redirect,
    current_app,
    url_for,
    Response,
    make_response,
    abort,
)
from typing import Any, Mapping
from nacl import pwhash
from cachetools import TTLCache
from llamora.app.services.auth_helpers import (
    login_required,
    sanitize_return_path,
)
from llamora.app.services.session_context import get_session_context
from llamora.app.services.validators import validate_password, PasswordValidationError
from llamora.app.services.crypto import (
    generate_dek,
    wrap_key,
    unwrap_key,
    generate_recovery_code,
    format_recovery_code,
)
from llamora.app.services.container import get_services
from llamora.settings import settings
import re
import orjson
from zxcvbn import zxcvbn
from llamora.app.services.time import local_date

auth_bp = Blueprint("auth", __name__)

_login_failures: TTLCache = TTLCache(
    maxsize=int(settings.AUTH.login_failure_cache_size),
    ttl=int(settings.AUTH.login_lockout_ttl),
)


PASSWORD_ERROR_MESSAGES: dict[PasswordValidationError, str] = {
    PasswordValidationError.MISSING: "All fields are required",
    PasswordValidationError.MAX_LENGTH: "Input exceeds max length",
    PasswordValidationError.MISMATCH: "Passwords do not match",
    PasswordValidationError.WEAK: "Password is too weak",
}


def _password_error_message(error: PasswordValidationError | None) -> str:
    if not error:
        return "Invalid input"
    return PASSWORD_ERROR_MESSAGES.get(error, "Invalid input")


def _length_error(
    username: str, password: str | None, *, max_user: int, max_pass: int | None
) -> str | None:
    if len(username) > max_user:
        return "Input exceeds max length"
    if password is not None and max_pass is not None and len(password) > max_pass:
        return "Input exceeds max length"
    return None


async def _render_auth_error(template: str, error: str, **context: Any):
    return await render_template(template, error=error, **context)


async def _issue_auth_response(
    user_id: str | int, dek: bytes, redirect_url: str
) -> Response:
    redirect_value = redirect(redirect_url)
    resp = await make_response(redirect_value)
    assert isinstance(resp, Response)
    session = get_session_context()
    manager = session.manager
    manager.set_secure_cookie(resp, "uid", str(user_id))
    manager.set_dek(resp, dek)
    return resp


async def _hash_password(password: bytes) -> bytes:
    return await asyncio.to_thread(pwhash.argon2id.str, password)


async def _verify_password(hash_bytes: bytes, password: bytes) -> bool:
    return await asyncio.to_thread(pwhash.argon2id.verify, hash_bytes, password)


def _get_client_ip() -> str:
    trusted_hops = int(settings.get("PROXY.trusted_hops") or 0)
    if trusted_hops > 0:
        access_route = getattr(request, "access_route", None)
        if access_route:
            return str(access_route[0]).strip()
    return request.remote_addr or "unknown"


async def _render_profile_page(user: Mapping[str, Any], **context):
    context["user"] = user
    state = await get_services().db.users.get_state(user["id"])
    context["day"] = state.get("active_date", local_date().isoformat())
    if request.headers.get("HX-Request"):
        return await render_template("partials/profile.html", **context)
    return await render_template(
        "index.html",
        content_template="partials/profile.html",
        **context,
    )


@auth_bp.route("/password_strength", methods=["POST"])
async def password_strength_check():
    form = await request.form
    field = form.get("password_field", "password")
    raw = form.get(field, "") or ""
    pw = raw.strip()[: int(settings.LIMITS.max_password_length)]

    # Short-circuit obviously empty input to avoid zxcvbn edge-case crashes
    if not pw:
        score = 0
        percent = 0
        html = await render_template(
            "partials/password_strength.html",
            score=score,
            percent=percent,
            suggestions=[],
        )
        return html

    suggestions: list[str] = []
    try:
        strength = await asyncio.to_thread(zxcvbn, pw)
        score = int(strength.get("score", 0))
        feedback = strength.get("feedback")
        if not isinstance(feedback, dict):
            current_app.logger.warning("Unexpected zxcvbn response: %r", strength)
        else:
            suggestions = feedback.get("suggestions") or []
            if not isinstance(suggestions, list):
                current_app.logger.warning(
                    "Unexpected zxcvbn suggestions: %r", feedback
                )
                suggestions = []
    except Exception as e:
        # Be defensive: never 500 due to the estimator
        current_app.logger.warning("zxcvbn failed: %r", e)
        score = 0
        suggestions = []

    percent = min(100, score * 25)
    html = await render_template(
        "partials/password_strength.html",
        score=score,
        percent=percent,
        suggestions=suggestions,
    )
    return html


@auth_bp.route("/register", methods=["GET", "POST"])
async def register():
    if settings.FEATURES.disable_registration:
        token = request.args.get("token")
        reg_token = current_app.config.get("REGISTRATION_TOKEN")
        if not reg_token or token != reg_token:
            abort(404)

    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "")
        confirm = form.get("confirm_password", "")

        max_user = int(settings.LIMITS.max_username_length)
        max_pass = int(settings.LIMITS.max_password_length)

        # Basic validations
        if not username:
            return await _render_auth_error(
                "register.html", error="All fields are required", username=username
            )

        password_error = await validate_password(
            password,
            confirm=confirm,
            require_confirm=True,
            max_length=max_pass,
            min_strength=3,
        )
        if password_error:
            message = _password_error_message(password_error)
            return await _render_auth_error(
                "register.html", error=message, username=username
            )

        length_error = _length_error(
            username, password, max_user=max_user, max_pass=max_pass
        )
        if length_error:
            return await _render_auth_error(
                "register.html", error=length_error, username=username
            )

        if not re.fullmatch(r"^[A-Za-z0-9_]+$", username):
            return await _render_auth_error(
                "register.html",
                error="Username may only contain letters, digits, and underscores",
                username=username,
            )

        db = get_services().db
        if await db.users.get_user_by_username(username):
            return await _render_auth_error(
                "register.html", error="Username already exists", username=username
            )

        password_bytes = password.encode("utf-8")
        hash_bytes = await _hash_password(password_bytes)
        password_hash = hash_bytes.decode("utf-8")

        dek = generate_dek()
        recovery_code = generate_recovery_code()
        pw_salt, pw_nonce, pw_cipher = wrap_key(dek, password)
        rc_salt, rc_nonce, rc_cipher = wrap_key(dek, recovery_code)

        user_id = await db.users.create_user(
            username,
            password_hash,
            pw_salt,
            pw_nonce,
            pw_cipher,
            rc_salt,
            rc_nonce,
            rc_cipher,
        )

        if settings.FEATURES.disable_registration:
            current_app.config["REGISTRATION_TOKEN"] = None

        return await _issue_auth_response(user_id, dek, url_for("days.index"))

    return await render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
async def login():
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "")
        return_url = sanitize_return_path(
            form.get("return") or request.args.get("return")
        )
        current_app.logger.debug("Login attempt for %s", username)

        client_ip = _get_client_ip()
        cache_key = (username or "", client_ip)
        attempts = _login_failures.get(cache_key, 0)
        if attempts >= int(settings.AUTH.max_login_attempts):
            current_app.logger.warning(
                "Login locked for %s from %s after %s attempts",
                username,
                client_ip,
                attempts,
            )
            return Response("Too many login attempts. Try again later.", status=429)

        max_user = int(settings.LIMITS.max_username_length)
        max_pass = int(settings.LIMITS.max_password_length)

        length_error = _length_error(
            username, password, max_user=max_user, max_pass=max_pass
        )
        if length_error:
            _login_failures[cache_key] = attempts + 1
            return await _render_auth_error(
                "login.html",
                error="Invalid credentials",
                return_url=return_url,
                username=username,
            )

        services = get_services()
        db = services.db
        user = await db.users.get_user_by_username(username)
        if user:
            try:
                await _verify_password(
                    user["password_hash"].encode("utf-8"),
                    password.encode("utf-8"),
                )
                dek = unwrap_key(
                    user["dek_pw_cipher"],
                    user["dek_pw_salt"],
                    user["dek_pw_nonce"],
                    password,
                )
                redirect_url = return_url
                if not redirect_url:
                    state = await db.users.get_state(user["id"])
                    active_date = state.get("active_date")
                    if active_date:
                        redirect_url = url_for("days.day", date=active_date)
                    else:
                        redirect_url = "/"
                resp = await _issue_auth_response(user["id"], dek, redirect_url)
                current_app.add_background_task(
                    services.search_api.warm_index,
                    str(user["id"]),
                    dek,
                )
                if cache_key in _login_failures:
                    del _login_failures[cache_key]
                current_app.logger.debug(
                    "Login succeeded for %s, redirecting to %s", username, redirect_url
                )
                return resp
            except Exception:
                current_app.logger.debug(
                    "Login verification failed for %s", username, exc_info=True
                )
                pass
        current_app.logger.debug("Login failed for %s", username)
        _login_failures[cache_key] = attempts + 1
        return await render_template(
            "login.html",
            error="Invalid credentials",
            return_url=return_url,
            username=username,
        )

    return_url = sanitize_return_path(request.args.get("return"))
    return await render_template("login.html", return_url=return_url)


@auth_bp.route("/logout", methods=["POST"])
async def logout():
    session = get_session_context()
    manager = session.manager
    user = await session.current_user()
    current_app.logger.debug("Logout for user %s", user["id"] if user else None)
    next_url = "/login"
    redirect_value = redirect(next_url)
    resp = await make_response(redirect_value)
    assert isinstance(resp, Response)

    manager.clear_session_dek()
    manager.clear_secure_cookie(resp)
    return resp


@auth_bp.route("/reset", methods=["GET", "POST"])
async def reset_password():
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        recovery = form.get("recovery_code", "").replace("-", "").upper()
        password = form.get("new_password", "")
        confirm = form.get("confirm_password", "")

        max_user = int(settings.LIMITS.max_username_length)
        max_pass = int(settings.LIMITS.max_password_length)
        min_pass = int(settings.LIMITS.min_password_length)

        if not username or not recovery:
            return await _render_auth_error(
                "reset_password.html", error="Invalid input"
            )

        length_error = _length_error(username, None, max_user=max_user, max_pass=None)
        if length_error:
            return await _render_auth_error(
                "reset_password.html", error="Invalid input"
            )

        password_error = await validate_password(
            password,
            confirm=confirm,
            require_confirm=True,
            min_length=min_pass,
            max_length=max_pass,
            require_letter=True,
            require_digit=True,
        )
        if password_error:
            return await _render_auth_error(
                "reset_password.html", error="Invalid input"
            )

        db = get_services().db
        user = await db.users.get_user_by_username(username)
        if not user:
            return await _render_auth_error(
                "reset_password.html", error="Invalid credentials"
            )

        try:
            dek = unwrap_key(
                user["dek_rc_cipher"],
                user["dek_rc_salt"],
                user["dek_rc_nonce"],
                recovery,
            )
        except Exception:
            return await _render_auth_error(
                "reset_password.html", error="Invalid recovery code"
            )

        password_bytes = password.encode("utf-8")
        hash_bytes = await _hash_password(password_bytes)
        password_hash = hash_bytes.decode("utf-8")
        pw_salt, pw_nonce, pw_cipher = wrap_key(dek, password)
        await db.users.update_password_wrap(
            user["id"], password_hash, pw_salt, pw_nonce, pw_cipher
        )
        return redirect("/login")

    return await render_template("reset_password.html")


@auth_bp.route("/profile")
@login_required
async def profile():
    session = get_session_context()
    user = await session.require_user()
    await get_services().db.users.update_state(user["id"], active_date=None)
    return await _render_profile_page(user)


@auth_bp.route("/profile/data")
@login_required
async def download_user_data():
    session = get_session_context()
    user = await session.require_user()
    dek = await session.dek()
    if dek is None:
        response = await make_response("Missing encryption key")
        assert isinstance(response, Response)
        response.status_code = 400
        return response

    messages = await get_services().db.entries.get_latest_entries(
        user["id"], 1000000, dek
    )
    user_data = {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "created_at": user["created_at"],
        },
        "messages": messages,
    }

    payload = orjson.dumps(user_data)
    headers = {"Content-Disposition": "attachment; filename=user_data.json"}
    response = await make_response(payload)
    assert isinstance(response, Response)
    response.headers.update(headers)
    response.mimetype = "application/json"
    return response


@auth_bp.route("/profile/password", methods=["POST"])
@login_required
async def change_password():
    session = get_session_context()
    user = await session.require_user()
    form = await request.form
    current = form.get("current_password", "")
    new = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    max_pass = int(settings.LIMITS.max_password_length)
    if not current:
        return await _render_profile_page(user, pw_error="All fields are required")

    if len(current) > max_pass:
        return await _render_profile_page(user, pw_error="Input exceeds max length")

    password_error = await validate_password(
        new,
        confirm=confirm,
        require_confirm=True,
        max_length=max_pass,
        min_strength=3,
    )
    if password_error:
        message = _password_error_message(password_error)
        return await _render_profile_page(user, pw_error=message)

    try:
        await _verify_password(
            user["password_hash"].encode("utf-8"), current.encode("utf-8")
        )
    except Exception:
        return await _render_profile_page(user, pw_error="Invalid current password")

    dek = await session.dek()
    if dek is None:
        return await _render_profile_page(user, pw_error="Missing encryption key")

    password_bytes = new.encode("utf-8")
    hash_bytes = await _hash_password(password_bytes)
    password_hash = hash_bytes.decode("utf-8")
    pw_salt, pw_nonce, pw_cipher = wrap_key(dek, new)
    await get_services().db.users.update_password_wrap(
        user["id"], password_hash, pw_salt, pw_nonce, pw_cipher
    )

    return await _render_profile_page(user, pw_success=True)


@auth_bp.route("/profile/recovery", methods=["POST"])
@login_required
async def regen_recovery():
    session = get_session_context()
    user = await session.require_user()
    dek = await session.dek()
    if dek is None:
        return await _render_profile_page(user, rc_error="Missing encryption key")

    recovery_code = generate_recovery_code()
    rc_salt, rc_nonce, rc_cipher = wrap_key(dek, recovery_code)
    await get_services().db.users.update_recovery_wrap(
        user["id"], rc_salt, rc_nonce, rc_cipher
    )

    return await render_template(
        "recovery.html",
        code=format_recovery_code(recovery_code),
        next_url=url_for("auth.profile"),
    )


@auth_bp.route("/profile", methods=["DELETE"])
@login_required
async def delete_profile():
    session = get_session_context()
    manager = session.manager
    user = await session.require_user()
    await get_services().db.users.delete_user(user["id"])
    resp = await make_response("", 204)
    assert isinstance(resp, Response)
    manager.clear_session_dek()
    if manager.dek_storage == "session":
        current_app.logger.debug(
            "Purged session DEK for deleted account %s", user["id"]
        )
    manager.clear_secure_cookie(resp)
    resp.headers["HX-Redirect"] = "/login"
    return resp
