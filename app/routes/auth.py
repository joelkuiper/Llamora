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
from nacl import pwhash
from app.services.auth_helpers import (
    set_secure_cookie,
    login_required,
    get_current_user,
    get_dek,
    clear_secure_cookie,
    set_dek,
    clear_session_dek,
)
from app.services.crypto import (
    generate_dek,
    wrap_key,
    unwrap_key,
    generate_recovery_code,
    format_recovery_code,
)
from app import db
import re
import config
import orjson
from zxcvbn import zxcvbn
from datetime import datetime
from app.services.time import local_date

auth_bp = Blueprint("auth", __name__)


async def _render_profile_page(user, **context):
    context["user"] = user
    state = await db.get_state(user["id"])
    context["day"] = state.get("active_date", local_date().isoformat())
    if request.headers.get("HX-Request"):
        return await render_template("partials/profile.html", **context)
    return await render_template(
        "index.html",
        sessions=[],
        content_template="partials/profile.html",
        **context,
    )


@auth_bp.route("/password_strength", methods=["POST"])
async def password_strength_check():
    form = await request.form
    field = form.get("password_field", "password")
    raw = form.get(field, "") or ""
    pw = raw.strip()[: config.MAX_PASSWORD_LENGTH]

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
        strength = zxcvbn(pw)
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
    if current_app.config.get("DISABLE_REGISTRATION"):
        token = request.args.get("token")
        reg_token = current_app.config.get("REGISTRATION_TOKEN")
        if not reg_token or token != reg_token:
            abort(404)

    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "")
        confirm = form.get("confirm_password", "")

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]

        # Basic validations
        if not username or not password or not confirm:
            return await render_template(
                "register.html", error="All fields are required"
            )

        if len(username) > max_user or len(password) > max_pass:
            return await render_template(
                "register.html", error="Input exceeds max length"
            )

        if not re.fullmatch(r"^[A-Za-z0-9_]+$", username):
            return await render_template(
                "register.html",
                error="Username may only contain letters, digits, and underscores",
            )

        if password != confirm:
            return await render_template(
                "register.html", error="Passwords do not match"
            )

        strength = zxcvbn(password)
        if strength.get("score", 0) < 3:
            return await render_template("register.html", error="Password is too weak")

        if await db.get_user_by_username(username):
            return await render_template(
                "register.html", error="Username already exists"
            )

        password_bytes = password.encode("utf-8")
        hash_bytes = pwhash.argon2id.str(password_bytes)
        password_hash = hash_bytes.decode("utf-8")

        dek = generate_dek()
        recovery_code = generate_recovery_code()
        pw_salt, pw_nonce, pw_cipher = wrap_key(dek, password)
        rc_salt, rc_nonce, rc_cipher = wrap_key(dek, recovery_code)

        await db.create_user(
            username,
            password_hash,
            pw_salt,
            pw_nonce,
            pw_cipher,
            rc_salt,
            rc_nonce,
            rc_cipher,
        )

        # Fetch the newly created user so we can establish a session
        user = await db.get_user_by_username(username)

        if current_app.config.get("DISABLE_REGISTRATION"):
            current_app.config["REGISTRATION_TOKEN"] = None

        html = await render_template(
            "recovery.html",
            code=format_recovery_code(recovery_code),
            next_url=url_for("days.index"),
        )
        resp = await make_response(html)
        set_secure_cookie(resp, "uid", str(user["id"]))
        set_dek(resp, dek)
        return resp

    return await render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
async def login():
    def _safe_return(url: str | None) -> str | None:
        if url and url.startswith("/") and not url.startswith("//"):
            return url
        return None

    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "")
        return_url = _safe_return(form.get("return") or request.args.get("return"))
        current_app.logger.debug("Login attempt for %s", username)

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]

        if len(username) > max_user or len(password) > max_pass:
            return await render_template(
                "login.html", error="Invalid credentials", return_url=return_url
            )

        user = await db.get_user_by_username(username)
        if user:
            try:
                pwhash.argon2id.verify(
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
                    state = await db.get_state(user["id"])
                    active_date = state.get("active_date")
                    if active_date:
                        redirect_url = url_for(
                            "days.day", date=active_date
                        )
                    else:
                        redirect_url = "/"
                resp = redirect(redirect_url)
                set_secure_cookie(resp, "uid", str(user["id"]))
                set_dek(resp, dek)
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
        return await render_template(
            "login.html", error="Invalid credentials", return_url=return_url
        )

    return_url = _safe_return(request.args.get("return"))
    return await render_template("login.html", return_url=return_url)


@auth_bp.route("/logout", methods=["POST"])
async def logout():
    user = await get_current_user()
    current_app.logger.debug("Logout for user %s", user["id"] if user else None)
    next_url = "/login"
    resp = redirect(next_url)

    clear_session_dek()
    clear_secure_cookie(resp)
    return resp


@auth_bp.route("/reset", methods=["GET", "POST"])
async def reset_password():
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        recovery = form.get("recovery_code", "").replace("-", "").upper()
        password = form.get("new_password", "")
        confirm = form.get("confirm_password", "")

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]
        min_pass = current_app.config["MIN_PASSWORD_LENGTH"]

        if (
            not username
            or not recovery
            or not password
            or not confirm
            or len(username) > max_user
            or len(password) > max_pass
            or len(password) < min_pass
            or password != confirm
            or not re.search(r"[A-Za-z]", password)
            or not re.search(r"\d", password)
        ):
            return await render_template("reset_password.html", error="Invalid input")

        user = await db.get_user_by_username(username)
        if not user:
            return await render_template(
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
            return await render_template(
                "reset_password.html", error="Invalid recovery code"
            )

        password_bytes = password.encode("utf-8")
        hash_bytes = pwhash.argon2id.str(password_bytes)
        password_hash = hash_bytes.decode("utf-8")
        pw_salt, pw_nonce, pw_cipher = wrap_key(dek, password)
        await db.update_password_wrap(
            user["id"], password_hash, pw_salt, pw_nonce, pw_cipher
        )
        return redirect("/login")

    return await render_template("reset_password.html")


@auth_bp.route("/profile")
@login_required
async def profile():
    user = await get_current_user()
    await db.update_state(user["id"], active_date=None)
    return await _render_profile_page(user)


@auth_bp.route("/profile/data")
@login_required
async def download_user_data():
    user = await get_current_user()
    dek = get_dek()
    if not dek:
        return Response("Missing encryption key", status=400)

    messages = await db.get_latest_messages(user["id"], 1000000, dek)
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
    return Response(payload, headers=headers, mimetype="application/json")


@auth_bp.route("/profile/password", methods=["POST"])
@login_required
async def change_password():
    user = await get_current_user()
    form = await request.form
    current = form.get("current_password", "")
    new = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    max_pass = current_app.config["MAX_PASSWORD_LENGTH"]
    if not current or not new or not confirm:
        return await _render_profile_page(user, pw_error="All fields are required")

    if len(current) > max_pass or len(new) > max_pass:
        return await _render_profile_page(user, pw_error="Input exceeds max length")

    if new != confirm:
        return await _render_profile_page(user, pw_error="Passwords do not match")

    strength = zxcvbn(new)
    if strength.get("score", 0) < 3:
        return await _render_profile_page(user, pw_error="Password is too weak")

    try:
        pwhash.argon2id.verify(
            user["password_hash"].encode("utf-8"), current.encode("utf-8")
        )
    except Exception:
        return await _render_profile_page(user, pw_error="Invalid current password")

    dek = get_dek()
    if not dek:
        return await _render_profile_page(user, pw_error="Missing encryption key")

    password_bytes = new.encode("utf-8")
    hash_bytes = pwhash.argon2id.str(password_bytes)
    password_hash = hash_bytes.decode("utf-8")
    pw_salt, pw_nonce, pw_cipher = wrap_key(dek, new)
    await db.update_password_wrap(
        user["id"], password_hash, pw_salt, pw_nonce, pw_cipher
    )

    return await _render_profile_page(user, pw_success=True)


@auth_bp.route("/profile/recovery", methods=["POST"])
@login_required
async def regen_recovery():
    user = await get_current_user()
    dek = get_dek()
    if not dek:
        return await _render_profile_page(user, rc_error="Missing encryption key")

    recovery_code = generate_recovery_code()
    rc_salt, rc_nonce, rc_cipher = wrap_key(dek, recovery_code)
    await db.update_recovery_wrap(user["id"], rc_salt, rc_nonce, rc_cipher)

    return await render_template(
        "recovery.html",
        code=format_recovery_code(recovery_code),
        next_url=url_for("auth.profile"),
    )


@auth_bp.route("/profile", methods=["DELETE"])
@login_required
async def delete_profile():
    user = await get_current_user()
    await db.delete_user(user["id"])
    resp = Response(status=204)
    clear_secure_cookie(resp)
    resp.headers["HX-Redirect"] = "/login"
    return resp
