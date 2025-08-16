from quart import (
    Blueprint,
    render_template,
    request,
    redirect,
    current_app,
    url_for,
    Response,
)
from nacl import pwhash
from app.services.auth_helpers import (
    set_secure_cookie,
    login_required,
    get_current_user,
    get_dek,
    clear_secure_cookie,
)
from app.services.crypto import generate_dek, wrap_key, unwrap_key
from app import db
import re
import base64
import secrets
import json
from zxcvbn import zxcvbn

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/password_strength", methods=["POST"])
async def password_strength_check():
    form = await request.form
    pw = form.get("password", "") or ""

    # Short-circuit obviously empty input to avoid zxcvbn edge-case crashes
    if not pw:
        score = 0
        percent = 0
        html = await render_template(
            "partials/password_strength.html", score=score, percent=percent
        )
        return html

    try:
        strength = zxcvbn(pw)
        score = int(strength.get("score", 0))
        suggestions = strength.get("feedback").get("suggestions")
    except Exception as e:
        # Be defensive: never 500 due to the estimator
        current_app.logger.warning("zxcvbn failed: %r", e)
        score = 0

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
        recovery_code = secrets.token_hex(16)
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

        return await render_template(
            "recovery.html", code=recovery_code, next_url=url_for("auth.login")
        )

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
                    active_session = state.get("active_session")
                    if active_session and await db.get_session(
                        user["id"], active_session
                    ):
                        redirect_url = url_for(
                            "sessions.session", session_id=active_session
                        )
                    else:
                        redirect_url = "/"
                resp = redirect(redirect_url)
                set_secure_cookie(resp, "uid", str(user["id"]))
                set_secure_cookie(resp, "dek", base64.b64encode(dek).decode("utf-8"))
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


@auth_bp.route("/logout")
async def logout():
    user = await get_current_user()
    current_app.logger.debug(
        "Logout for user %s", user["id"] if user else None
    )
    resp = redirect("/login")
    clear_secure_cookie(resp)
    return resp


@auth_bp.route("/reset", methods=["GET", "POST"])
async def reset_password():
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        recovery = form.get("recovery_code", "")
        password = form.get("new_password", "")
        confirm = form.get("confirm_password", "")

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]
        min_pass = current_app.config.get("MIN_PASSWORD_LENGTH")

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
    state = await db.get_state(user["id"])
    active_session = state.get("active_session")
    if active_session and not await db.get_session(user["id"], active_session):
        active_session = None
    return await render_template(
        "profile.html", user=user, active_session=active_session
    )


@auth_bp.route("/profile/data")
@login_required
async def download_user_data():
    user = await get_current_user()
    dek = get_dek()
    if not dek:
        return Response("Missing encryption key", status=400)

    sessions = await db.get_all_sessions(user["id"])
    data_sessions = []
    for session in sessions:
        history = await db.get_history(user["id"], session["id"], dek)
        data_sessions.append(
            {
                "id": session["id"],
                "name": session["name"],
                "created_at": session["created_at"],
                "messages": history,
            }
        )

    user_data = {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "created_at": user["created_at"],
        },
        "sessions": data_sessions,
    }

    payload = json.dumps(user_data)
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
    min_pass = current_app.config.get("MIN_PASSWORD_LENGTH")

    if (
        not current
        or not new
        or not confirm
        or len(current) > max_pass
        or len(new) > max_pass
        or new != confirm
        or len(new) < min_pass
        or not re.search(r"[A-Za-z]", new)
        or not re.search(r"\d", new)
    ):
        return await render_template(
            "profile.html", user=user, pw_error="Invalid input"
        )

    try:
        pwhash.argon2id.verify(
            user["password_hash"].encode("utf-8"), current.encode("utf-8")
        )
    except Exception:
        return await render_template(
            "profile.html", user=user, pw_error="Invalid current password"
        )

    dek = get_dek()
    if not dek:
        return await render_template(
            "profile.html", user=user, pw_error="Missing encryption key"
        )

    password_bytes = new.encode("utf-8")
    hash_bytes = pwhash.argon2id.str(password_bytes)
    password_hash = hash_bytes.decode("utf-8")
    pw_salt, pw_nonce, pw_cipher = wrap_key(dek, new)
    await db.update_password_wrap(
        user["id"], password_hash, pw_salt, pw_nonce, pw_cipher
    )

    return await render_template("profile.html", user=user, pw_success=True)


@auth_bp.route("/profile/recovery", methods=["POST"])
@login_required
async def regen_recovery():
    user = await get_current_user()
    dek = get_dek()
    if not dek:
        return await render_template(
            "profile.html", user=user, rc_error="Missing encryption key"
        )

    recovery_code = secrets.token_hex(16)
    rc_salt, rc_nonce, rc_cipher = wrap_key(dek, recovery_code)
    await db.update_recovery_wrap(user["id"], rc_salt, rc_nonce, rc_cipher)

    return await render_template(
        "recovery.html", code=recovery_code, next_url=url_for("auth.profile")
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
