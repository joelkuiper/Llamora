import os
import base64
from quart import Response, request, redirect
from urllib.parse import urlparse, quote
from functools import wraps
from nacl import secret
from app import db

cookie_secret = os.environ.get("LLAMORA_COOKIE_SECRET")
if not cookie_secret or len(cookie_secret) % 4 != 0:
    raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string")

try:
    cookie_key = base64.b64decode(cookie_secret, altchars=b"-_", validate=True)
except Exception as exc:
    raise RuntimeError(
        "Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string"
    ) from exc

if len(cookie_key) != secret.SecretBox.KEY_SIZE:
    raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string")

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


def _safe_return_path() -> str:
    """Determine a safe path to return to after login."""

    if request.headers.get("HX-Request"):
        # HTMX sends the current URL in this header
        current = request.headers.get("HX-Current-URL", "/")
        parsed = urlparse(current)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
    else:
        # full_path includes trailing '?' if there was no query string
        path = request.full_path if request.query_string else request.path
    return path.rstrip("?") or "/"


def login_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        return_path = _safe_return_path()
        login_url = f"/login?return={quote(return_path, safe='') }"

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
