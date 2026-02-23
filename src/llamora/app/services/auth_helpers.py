from __future__ import annotations

import base64
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import TYPE_CHECKING, Any, Mapping

import orjson
from cachetools import TTLCache
from quart import Response, current_app, g, redirect, request
from urllib.parse import quote, urlparse
from nacl import secret

from llamora.app.services.container import get_services

if TYPE_CHECKING:
    from llamora.app.db.sessions import SessionsRepository

SECURE_COOKIE_MANAGER_KEY = "llamora_secure_cookie_manager"
_MISSING_USER = object()


class SecureCookieManager:
    """Manage secure cookie handling and DEK storage."""

    _cookie_state_attr = "_secure_cookie_state"
    _current_user_attr = "_current_user"
    _cookie_touch_key = "_last_seen"
    _cookie_touch_flag_attr = "_secure_cookie_touch_pending"
    _cookie_clear_flag_attr = "_secure_cookie_clear_pending"

    def __init__(
        self,
        *,
        cookie_name: str,
        cookie_secret: str,
        dek_storage: str,
        force_secure: bool = False,
        session_idle_ttl: int,
        cookie_touch_interval: int,
        user_cache_ttl: int = 60,
        user_cache_maxsize: int = 2048,
    ) -> None:
        key = self._decode_cookie_secret(cookie_secret)
        self.cookie_name = cookie_name
        self.cookie_box = secret.SecretBox(key)
        self.dek_storage = dek_storage.lower()
        self.force_secure = bool(force_secure)
        self._session_idle_ttl = max(0, int(session_idle_ttl))
        self._cookie_touch_interval = max(0, int(cookie_touch_interval))
        self._sessions_repo: SessionsRepository | None = None
        self._user_snapshot_cache: TTLCache[tuple[str, str], Any] = TTLCache(
            maxsize=user_cache_maxsize,
            ttl=user_cache_ttl,
        )

    @staticmethod
    def _decode_cookie_secret(raw_secret: str) -> bytes:
        secret_value = str(raw_secret or "")
        if not secret_value or len(secret_value) % 4 != 0:
            raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string")
        try:
            key = base64.b64decode(secret_value, altchars=b"-_", validate=True)
        except Exception as exc:  # pragma: no cover - defensive guard
            raise RuntimeError(
                "Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string"
            ) from exc
        if len(key) != secret.SecretBox.KEY_SIZE:
            raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string")
        return key

    def _get_cookie_data(self) -> dict[str, Any]:
        if hasattr(g, self._cookie_state_attr):
            return getattr(g, self._cookie_state_attr)

        raw = request.cookies.get(self.cookie_name)
        if not raw:
            current_app.logger.debug("No %s cookie present", self.cookie_name)
            return {}

        try:
            token = base64.urlsafe_b64decode(raw)
            decrypted = self.cookie_box.decrypt(token).decode("utf-8")
            data = orjson.loads(decrypted)
            setattr(g, self._cookie_state_attr, data)
            current_app.logger.debug(
                "Loaded %s cookie with keys %s",
                self.cookie_name,
                list(data.keys()),
            )
            return data
        except Exception:
            current_app.logger.debug(
                "Failed to decode %s cookie",
                self.cookie_name,
                exc_info=True,
            )
            return {}

    def _cookie_state(self) -> dict[str, Any]:
        if not hasattr(g, self._cookie_state_attr):
            setattr(g, self._cookie_state_attr, self._get_cookie_data())
        return getattr(g, self._cookie_state_attr)

    @staticmethod
    def _coerce_timestamp(value: Any) -> int | None:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return None
        return timestamp if timestamp >= 0 else None

    def _annotate_cookie_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data)
        if payload and self._session_idle_ttl > 0:
            payload[self._cookie_touch_key] = int(time.time())
        else:
            payload.pop(self._cookie_touch_key, None)
        return payload

    def _should_refresh_cookie(self) -> bool:
        if self._session_idle_ttl <= 0:
            return False
        state = self._cookie_state()
        if not state or not state.get("uid"):
            return False
        if self._cookie_touch_interval <= 0:
            return True
        now = int(time.time())
        last_seen = self._coerce_timestamp(state.get(self._cookie_touch_key))
        if last_seen is None:
            return True
        return (now - last_seen) >= self._cookie_touch_interval

    def mark_authenticated_activity(self) -> None:
        if self._should_refresh_cookie():
            setattr(g, self._cookie_touch_flag_attr, True)

    def request_cookie_clear(self) -> None:
        setattr(g, self._cookie_clear_flag_attr, True)

    def finalize_response(self, response: Response) -> Response:
        if getattr(g, self._cookie_clear_flag_attr, False):
            self.clear_secure_cookie(response)
            return response

        if getattr(g, self._cookie_touch_flag_attr, False):
            state = self._cookie_state()
            if state:
                return self._set_cookie_data(response, state)
        return response

    def _set_cookie_data(self, response: Response, data: dict[str, Any]) -> Response:
        if not data:
            current_app.logger.debug("Clearing %s cookie", self.cookie_name)
            response.delete_cookie(self.cookie_name, path="/", samesite="Lax")
            if hasattr(g, self._cookie_state_attr):
                setattr(g, self._cookie_state_attr, {})
            setattr(g, self._cookie_touch_flag_attr, False)
            setattr(g, self._cookie_clear_flag_attr, False)
            return response

        payload = self._annotate_cookie_payload(data)
        token = self.cookie_box.encrypt(orjson.dumps(payload))
        b64 = base64.urlsafe_b64encode(token).decode("utf-8")
        current_app.logger.debug(
            "Setting %s cookie (secure=%s) with keys %s",
            self.cookie_name,
            request.is_secure or self.force_secure,
            list(payload.keys()),
        )
        max_age: int | None = None
        expires: datetime | None = None
        if self._session_idle_ttl > 0:
            max_age = self._session_idle_ttl
            expires = datetime.now(timezone.utc) + timedelta(
                seconds=self._session_idle_ttl
            )

        response.set_cookie(
            self.cookie_name,
            b64,
            httponly=True,
            secure=request.is_secure or self.force_secure,
            samesite="Lax",
            path="/",
            max_age=max_age,
            expires=expires,
        )
        setattr(g, self._cookie_state_attr, payload)
        setattr(g, self._cookie_touch_flag_attr, False)
        setattr(g, self._cookie_clear_flag_attr, False)
        return response

    def get_secure_cookie(self, name: str) -> str | None:
        if hasattr(g, self._cookie_state_attr):
            state = getattr(g, self._cookie_state_attr)
        else:
            state = self._get_cookie_data()
        return state.get(name) if isinstance(state, dict) else None

    def set_secure_cookie(
        self, response: Response, name: str, value: str | None
    ) -> Response:
        state = self._cookie_state()
        if value is None:
            state.pop(name, None)
        else:
            state[name] = value
        return self._set_cookie_data(response, state)

    def clear_secure_cookie(self, response: Response) -> None:
        current_app.logger.debug("Deleting %s cookie", self.cookie_name)
        response.delete_cookie(self.cookie_name, path="/", samesite="Lax")
        if hasattr(g, self._cookie_state_attr):
            setattr(g, self._cookie_state_attr, {})
        setattr(g, self._cookie_touch_flag_attr, False)
        setattr(g, self._cookie_clear_flag_attr, False)

    async def set_dek(self, response: Response, dek: bytes) -> None:
        if self.dek_storage == "session":
            assert self._sessions_repo is not None
            sid = secrets.token_urlsafe(32)
            await self._sessions_repo.store(sid, dek)
            self.set_secure_cookie(response, "sid", sid)
            self.set_secure_cookie(response, "dek", None)
        else:
            encoded = base64.b64encode(dek).decode("utf-8")
            self.set_secure_cookie(response, "dek", encoded)

    async def clear_session_dek(self) -> None:
        if self.dek_storage == "session":
            assert self._sessions_repo is not None
            sid = self.get_secure_cookie("sid")
            if sid:
                await self._sessions_repo.remove(sid)

    async def clear_all_session_deks(self) -> int:
        if self.dek_storage != "session":
            return 0
        assert self._sessions_repo is not None
        return await self._sessions_repo.clear_all()

    def invalidate_user_snapshot(self, uid: str) -> None:
        """Remove all cached snapshots for *uid* so the next lookup hits the DB."""
        to_remove = [k for k in self._user_snapshot_cache if k[0] == uid]
        for k in to_remove:
            self._user_snapshot_cache.pop(k, None)

    def _user_cache_key(self, uid: str) -> tuple[str, str]:
        if self.dek_storage == "session":
            sid = self.get_secure_cookie("sid")
            return (uid, sid or "")
        dek = self.get_secure_cookie("dek")
        return (uid, dek or "")

    async def get_current_user(self) -> Mapping[str, Any] | None:
        if hasattr(g, self._current_user_attr):
            return getattr(g, self._current_user_attr)

        uid = self.get_secure_cookie("uid")
        if not uid:
            user = None
        else:
            cache_key = self._user_cache_key(uid)
            if cache_key in self._user_snapshot_cache:
                cached = self._user_snapshot_cache[cache_key]
                user = None if cached is _MISSING_USER else cached
            else:
                services = get_services()
                user = await services.db.users.get_user_by_id(uid)
                self._user_snapshot_cache[cache_key] = (
                    user if user is not None else _MISSING_USER
                )

        setattr(g, self._current_user_attr, user)
        return user

    async def get_dek(self) -> bytes | None:
        if self.dek_storage == "session":
            assert self._sessions_repo is not None
            sid = self.get_secure_cookie("sid")
            if not sid:
                return None
            return await self._sessions_repo.load(sid)

        state = self._cookie_state()
        data = state.get("dek")
        if not data:
            return None
        try:
            return base64.b64decode(data)
        except Exception:
            current_app.logger.info(
                "Invalid DEK cookie encountered; clearing cached credentials",
                exc_info=True,
            )
            state.pop("dek", None)

            # Clear session-based DEK storage if applicable
            if self.dek_storage == "session":
                sid = state.get("sid") or ""
                state.pop("sid", None)
                await self.clear_session_dek()
                cache_key_sid: str | None = sid
            else:
                cache_key_sid = None

            # Invalidate user snapshot cache
            uid = state.get("uid")
            if uid:
                if cache_key_sid is not None:
                    cache_key = (uid, cache_key_sid)
                else:
                    cache_key = (uid, data)
                self._user_snapshot_cache.pop(cache_key, None)

            return None

    async def load_user(self) -> None:
        endpoint = request.endpoint or ""
        if endpoint == "static" or endpoint.endswith(".static"):
            current_app.logger.debug(
                "Skipping user load for static endpoint %s", endpoint
            )
            return

        path = request.path
        if path.startswith("/static/") or path in {"/health", "/healthz", "/ready"}:
            current_app.logger.debug("Skipping user load for lightweight path %s", path)
            return

        user = await self.get_current_user()
        if user:
            dek = await self.get_dek()
            if dek is None:
                self.request_cookie_clear()
                current_app.logger.debug(
                    "User %s has no valid DEK for request; scheduling logout",
                    user["id"],
                )
                return
            self.mark_authenticated_activity()
            current_app.logger.debug("Loaded user %s for request", user["id"])
        else:
            current_app.logger.debug("No user loaded for request")


def get_secure_cookie_manager() -> SecureCookieManager:
    manager = current_app.extensions.get(SECURE_COOKIE_MANAGER_KEY)
    if manager is None:
        raise RuntimeError("Secure cookie manager is not initialised")
    return manager


def get_secure_cookie(name: str) -> str | None:
    return get_secure_cookie_manager().get_secure_cookie(name)


def set_secure_cookie(response: Response, name: str, value: str | None) -> Response:
    return get_secure_cookie_manager().set_secure_cookie(response, name, value)


def clear_secure_cookie(response: Response) -> None:
    get_secure_cookie_manager().clear_secure_cookie(response)


async def set_dek(response: Response, dek: bytes) -> None:
    await get_secure_cookie_manager().set_dek(response, dek)


async def clear_session_dek() -> None:
    await get_secure_cookie_manager().clear_session_dek()


def invalidate_user_snapshot(uid: str) -> None:
    get_secure_cookie_manager().invalidate_user_snapshot(uid)


async def get_current_user() -> Mapping[str, Any] | None:
    return await get_secure_cookie_manager().get_current_user()


async def get_dek() -> bytes | None:
    return await get_secure_cookie_manager().get_dek()


def sanitize_return_path(raw: str | None) -> str | None:
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return None


def _safe_return_path() -> str:
    if request.headers.get("HX-Request"):
        current = request.headers.get("HX-Current-URL", "/")
        parsed = urlparse(current)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
    else:
        path = request.full_path if request.query_string else request.path
    return sanitize_return_path(path.rstrip("?")) or "/"


def login_required(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        manager = get_secure_cookie_manager()
        return_path = _safe_return_path()
        login_url = f"/login?return={quote(return_path, safe='')}"

        user = await manager.get_current_user()
        dek = (await manager.get_dek()) if user else None
        if not user or dek is None:
            if user and dek is None:
                manager.request_cookie_clear()
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


async def load_user() -> None:
    await get_secure_cookie_manager().load_user()


async def finalize_auth_session(response: Response) -> Response:
    return get_secure_cookie_manager().finalize_response(response)
