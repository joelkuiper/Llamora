import os
import base64
from flask import request, redirect
from functools import wraps
from nacl import secret
from app import db

cookie_secret = os.environ.get("CHAT_COOKIE_SECRET")
cookie_key = base64.urlsafe_b64decode(cookie_secret)
cookie_box = secret.SecretBox(cookie_key)


def set_secure_cookie(response, name, value):
    token = cookie_box.encrypt(value.encode("utf-8"))
    b64 = base64.urlsafe_b64encode(token).decode("utf-8")
    response.set_cookie(name, b64, httponly=True, secure=True, samesite="Lax")


def get_secure_cookie(name):
    data = request.cookies.get(name)
    if not data:
        return None
    try:
        token = base64.urlsafe_b64decode(data)
        return cookie_box.decrypt(token).decode("utf-8")
    except Exception:
        return None


def get_current_user():
    uid = get_secure_cookie("uid")
    return db.get_user_by_id(uid) if uid else None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return redirect("/login")
        return f(*args, **kwargs)

    return wrapper


def load_user():
    # Eager-load user info if needed in templates
    request.user = get_current_user()
