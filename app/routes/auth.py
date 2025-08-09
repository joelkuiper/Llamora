from quart import (
    Blueprint,
    render_template,
    request,
    redirect,
    current_app,
)
from nacl import pwhash
from app.services.auth_helpers import set_secure_cookie
from app.services.crypto import generate_dek, wrap_key, unwrap_key
from app import db
import re
import base64
import secrets

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
async def register():
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "")
        confirm = form.get("confirm_password", "")

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]
        min_pass = current_app.config.get("MIN_PASSWORD_LENGTH")

        # Basic validations
        if not username or not password or not confirm:
            return await render_template("register.html", error="All fields are required")

        if len(username) > max_user or len(password) > max_pass:
            return await render_template("register.html", error="Input exceeds max length")

        if not re.fullmatch(r"\S+", username):
            return await render_template(
                "register.html", error="Username may not contain spaces"
            )

        if not re.fullmatch(r"^[A-Za-z0-9_]+$", username):
            return await render_template(
                "register.html",
                error="Username may only contain letters, digits, and underscores",
            )

        if password != confirm:
            return await render_template("register.html", error="Passwords do not match")

        if len(password) < min_pass:
            return await render_template(
                "register.html",
                error=f"Password must be at least {min_pass} characters long",
            )

        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            return await render_template(
                "register.html",
                error="Password must contain at least one letter and one number",
            )

        if await db.get_user_by_username(username):
            return await render_template("register.html", error="Username already exists")

        # All good — create user
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

        return await render_template("recovery.html", code=recovery_code)

    return await render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
async def login():
    if request.method == "POST":
        form = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "")

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]

        if len(username) > max_user or len(password) > max_pass:
            return await render_template("login.html", error="Invalid credentials")

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
                resp = redirect("/")
                set_secure_cookie(resp, "uid", str(user["id"]))
                set_secure_cookie(
                    resp, "dek", base64.b64encode(dek).decode("utf-8")
                )
                return resp
            except Exception:
                pass
        return await render_template("login.html", error="Invalid credentials")
    return await render_template("login.html")


@auth_bp.route("/logout")
async def logout():
    resp = redirect("/login")
    resp.delete_cookie("uid")
    resp.delete_cookie("dek")
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
            return await render_template(
                "reset_password.html", error="Invalid input"
            )

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
