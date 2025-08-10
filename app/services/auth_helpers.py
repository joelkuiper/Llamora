import os
import base64
from quart import Response, request, redirect
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


async def get_current_user():
    uid = get_secure_cookie("uid")
    return await db.get_user_by_id(uid) if uid else None


def get_dek():
    data = get_secure_cookie("dek")
    if not data:
        return None
    try:
        return base64.b64decode(data)
    except Exception:
        return None


def login_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        login_url = "/login"
        if not await get_current_user():
            if request.headers.get("HX-Request"):
                resp = Response(status=401)
                resp.headers["HX-Redirect"] = login_url
                return resp
            return redirect(login_url)

        return await f(*args, **kwargs)

    return wrapper


async def load_user():
    # Eager-load user info if needed in templates
    request.user = await get_current_user()
