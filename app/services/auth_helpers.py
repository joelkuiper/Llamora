import os
import base64
import orjson
import secrets
from cachetools import TTLCache
from quart import Response, request, redirect, g, current_app
from urllib.parse import urlparse, quote
from functools import wraps
from nacl import secret
from app import db
from config import SESSION_TTL

cookie_secret = os.environ.get("LLAMORA_COOKIE_SECRET")
if not cookie_secret or len(cookie_secret) % 4 != 0:
    raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string")

try:
    cookie_key = base64.b64decode(cookie_secret, altchars=b"-_", validate=True)
except Exception as exc:
    raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string") from exc

if len(cookie_key) != secret.SecretBox.KEY_SIZE:
    raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string")

COOKIE_NAME = os.environ.get("LLAMORA_COOKIE_NAME", "llamora")
cookie_box = secret.SecretBox(cookie_key)

DEK_STORAGE = os.getenv("LLAMORA_DEK_STORAGE", "cookie").lower()

# Rough upper bound on concurrent sessions; adjust as needed
dek_store = TTLCache(maxsize=1024, ttl=SESSION_TTL)


def _get_cookie_data() -> dict:
    # If we've already got a merged state this request, return it
    if hasattr(g, "_secure_cookie_state"):
        return g._secure_cookie_state

    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        current_app.logger.debug("No %s cookie present", COOKIE_NAME)
        return {}

    try:
        token = base64.urlsafe_b64decode(raw)
        decrypted = cookie_box.decrypt(token).decode("utf-8")
        data = orjson.loads(decrypted)
        g._secure_cookie_state = data  # cache for later calls
        current_app.logger.debug(
            "Loaded %s cookie with keys %s", COOKIE_NAME, list(data.keys())
        )
        return data
    except Exception:
        current_app.logger.debug(
            "Failed to decode %s cookie", COOKIE_NAME, exc_info=True
        )
        return {}


def _cookie_state() -> dict:
    """Per-request merged cookie state."""
    if not hasattr(g, "_secure_cookie_state"):
        g._secure_cookie_state = _get_cookie_data()
    return g._secure_cookie_state


def _set_cookie_data(response: Response, data: dict) -> None:
    # If empty -> delete cookie to avoid storing an empty blob
    if not data:
        current_app.logger.debug("Clearing %s cookie", COOKIE_NAME)
        response.delete_cookie(COOKIE_NAME, path="/", samesite="Lax")
        return
    token = cookie_box.encrypt(orjson.dumps(data).encode("utf-8"))
    b64 = base64.urlsafe_b64encode(token).decode("utf-8")
    # Only mark the cookie as secure when the current request is served
    # over HTTPS. When running the application locally without TLS the
    # "secure" flag would prevent the browser from storing the cookie at all
    # which leads to a successful login immediately redirecting back to the
    # login page. Detect the scheme from the request so development setups
    # using plain HTTP continue to function while production deployments
    # still benefit from secure cookies.
    current_app.logger.debug(
        "Setting %s cookie (secure=%s) with keys %s",
        COOKIE_NAME,
        request.is_secure,
        list(data.keys()),
    )
    response.set_cookie(
        COOKIE_NAME,
        b64,
        httponly=True,
        secure=request.is_secure,
        samesite="Lax",
        path="/",
    )


def get_secure_cookie(name: str) -> str | None:
    # Prefer in-request state if we've already touched it
    if hasattr(g, "_secure_cookie_state"):
        return g._secure_cookie_state.get(name)
    return _get_cookie_data().get(name)


def set_secure_cookie(response: Response, name: str, value: str | None) -> None:
    state = _cookie_state()  # start from cached/merged state
    if value is None:
        state.pop(name, None)  # delete key
    else:
        state[name] = value  # merge key
    _set_cookie_data(response, state)  # write back the full, merged dict


def clear_secure_cookie(response: Response) -> None:
    # Ensure we delete the same cookie we set by matching its attributes.
    current_app.logger.debug("Deleting %s cookie", COOKIE_NAME)
    response.delete_cookie(COOKIE_NAME, path="/", samesite="Lax")


def set_dek(response: Response, dek: bytes) -> None:
    if DEK_STORAGE == "session":
        sid = secrets.token_urlsafe(32)
        dek_store[sid] = dek
        set_secure_cookie(response, "sid", sid)
        set_secure_cookie(response, "dek", None)
    else:
        set_secure_cookie(response, "dek", base64.b64encode(dek).decode("utf-8"))


def clear_session_dek() -> None:
    """Remove the DEK from the server-side store if session storage is used.

    When the DEK is stored in cookies, the entire secure cookie is cleared
    elsewhere so no extra work is needed here.
    """
    if DEK_STORAGE == "session":
        sid = get_secure_cookie("sid")
        if sid:
            dek_store.pop(sid, None)


async def get_current_user():
    uid = get_secure_cookie("uid")
    return await db.get_user_by_id(uid) if uid else None


def get_dek():
    """Retrieve the DEK from storage.

    In session mode this refreshes the entry's TTL so active sessions stay
    valid.
    """
    if DEK_STORAGE == "session":
        sid = get_secure_cookie("sid")
        if not sid:
            return None
        dek_store.expire()
        dek = dek_store.get(sid)
        if dek is not None:
            dek_store[sid] = dek  # refresh TTL
        return dek
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

        user = await get_current_user()
        dek = get_dek() if user else None
        if not user or not dek:
            current_app.logger.debug(
                "Unauthenticated access or missing DEK to %s", request.path
            )
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
    if request.user:
        _ = get_dek()  # refresh session DEK TTL if present
        current_app.logger.debug("Loaded user %s for request", request.user["id"])
    else:
        current_app.logger.debug("No user loaded for request")
