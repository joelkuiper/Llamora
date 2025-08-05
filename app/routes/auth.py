from flask import Blueprint, render_template, request, redirect, make_response
from nacl import pwhash
from app.services.auth_helpers import set_secure_cookie
from app import db

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template("register.html", error="Username and password required")
        if db.get_user_by_username(username):
            return render_template("register.html", error="Username already exists")
        password_hash = pwhash.argon2id.str(password.encode("utf-8")).decode("utf-8")
        db.create_user(username, password_hash)
        return redirect("/login")
    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
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
