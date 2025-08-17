import time
import config

_session_deks: dict[str, tuple[bytes, float]] = {}


def set_dek(user_id: str, dek: bytes) -> None:
    """Store a user's data encryption key with expiration."""
    _session_deks[user_id] = (dek, time.time() + config.SESSION_TTL_SECONDS)


def get_dek(user_id: str) -> bytes | None:
    """Retrieve a user's data encryption key if it hasn't expired."""
    item = _session_deks.get(user_id)
    if not item:
        return None
    dek, expires = item
    now = time.time()
    if expires < now:
        _session_deks.pop(user_id, None)
        return None
    _session_deks[user_id] = (dek, now + config.SESSION_TTL_SECONDS)
    return dek


def clear_dek(user_id: str) -> None:
    """Remove a user's data encryption key from the store."""
    _session_deks.pop(user_id, None)


def cleanup_expired() -> None:
    """Purge expired keys to prevent unbounded growth."""
    now = time.time()
    expired = [uid for uid, (_, exp) in _session_deks.items() if exp < now]
    for uid in expired:
        _session_deks.pop(uid, None)
