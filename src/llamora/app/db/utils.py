from __future__ import annotations

from functools import lru_cache
from typing import Callable

from llamora.app.util.tags import canonicalize


@lru_cache(maxsize=2048)
def cached_tag_name(
    user_id: str,
    tag_hash: bytes,
    name_nonce: bytes,
    name_ct: bytes,
    alg: bytes,
    dek: bytes,
    decrypt_message: Callable[[bytes, str, str, bytes, bytes, bytes], str],
) -> str:
    """Decrypt and cache tag names by hash."""

    plaintext = decrypt_message(dek, user_id, tag_hash.hex(), name_nonce, name_ct, alg)
    raw = (plaintext or "").strip()
    if not raw:
        return ""
    try:
        return canonicalize(raw)
    except ValueError:
        return ""
