from __future__ import annotations

from typing import Any, Mapping

from quart import abort, g

from llamora.app.services.auth_helpers import (
    SecureCookieManager,
    get_secure_cookie_manager,
)

_SESSION_CONTEXT_KEY = "llamora_session_context"


class SessionContext:
    """Cache per-request session artifacts such as the user snapshot and DEK."""

    __slots__ = ("manager", "_user", "_user_resolved", "_dek", "_dek_resolved")

    def __init__(self, manager: SecureCookieManager) -> None:
        self.manager = manager
        self._user: Mapping[str, Any] | None = None
        self._user_resolved = False
        self._dek: bytes | None = None
        self._dek_resolved = False

    async def current_user(self) -> Mapping[str, Any] | None:
        """Return the cached current user snapshot, if any."""

        if not self._user_resolved:
            self._user = await self.manager.get_current_user()
            self._user_resolved = True
        return self._user

    async def require_user(self) -> Mapping[str, Any]:
        """Return the current user or abort if none is authenticated."""

        user = await self.current_user()
        if user is None:
            abort(401)
            raise AssertionError("unreachable")
        return user

    def _resolve_dek(self) -> bytes | None:
        if not self._dek_resolved:
            self._dek = self.manager.get_dek()
            self._dek_resolved = True
        return self._dek

    async def dek(self) -> bytes | None:
        """Return the cached data encryption key, if available."""

        await self.current_user()
        return self._resolve_dek()

    async def require_dek(self) -> bytes:
        """Return the DEK or abort if it is unavailable."""

        await self.require_user()
        dek = self._resolve_dek()
        if dek is None:
            abort(401, description="Missing encryption key")
            raise AssertionError("unreachable")
        return dek


def get_session_context() -> SessionContext:
    """Return the lazily-initialised :class:`SessionContext` for the request."""

    ctx: SessionContext | None = getattr(g, _SESSION_CONTEXT_KEY, None)
    if ctx is None:
        ctx = SessionContext(get_secure_cookie_manager())
        setattr(g, _SESSION_CONTEXT_KEY, ctx)
    return ctx
