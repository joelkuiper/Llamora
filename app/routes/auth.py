from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    make_response,
    current_app,
)
from nacl import pwhash
from app.services.auth_helpers import set_secure_cookie
from app import db
import re

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]
        min_pass = current_app.config.get("MIN_PASSWORD_LENGTH")

        # Basic validations
        if not username or not password or not confirm:
            return render_template("register.html", error="All fields are required")

        if len(username) > max_user or len(password) > max_pass:
            return render_template("register.html", error="Input exceeds max length")

        if not re.fullmatch(r"\S+", username):
            return render_template(
                "register.html", error="Username may not contain spaces"
            )

        if not re.fullmatch(r"^[A-Za-z0-9_]+$", username):
            return render_template(
                "register.html",
                error="Username may only contain letters, digits, and underscores",
            )

        if password != confirm:
            return render_template("register.html", error="Passwords do not match")

        if len(password) < min_pass:
            return render_template(
                "register.html",
                error=f"Password must be at least {min_pass} characters long",
            )

        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            return render_template(
                "register.html",
                error="Password must contain at least one letter and one number",
            )

        if db.get_user_by_username(username):
            return render_template("register.html", error="Username already exists")

        # All good — create user
        password_bytes = password.encode("utf-8")
        hash_bytes = pwhash.argon2id.str(password_bytes)
        password_hash = hash_bytes.decode("utf-8")

        db.create_user(username, password_hash)
        return redirect("/login")

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        max_user = current_app.config["MAX_USERNAME_LENGTH"]
        max_pass = current_app.config["MAX_PASSWORD_LENGTH"]

        if len(username) > max_user or len(password) > max_pass:
            return render_template("login.html", error="Invalid credentials")

        user = db.get_user_by_username(username)
        if user:
            try:
                pwhash.argon2id.verify(
                    user["password_hash"].encode("utf-8"),
                    password.encode("utf-8"),
                )
                resp = redirect("/")
                set_secure_cookie(resp, "uid", str(user["id"]))
                return resp
            except Exception:
                pass
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    resp = redirect("/login")
    resp.delete_cookie("uid")
    return resp
