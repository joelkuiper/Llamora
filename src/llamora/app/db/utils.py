from __future__ import annotations

from datetime import date
from llamora.app.util.tags import canonicalize
from llamora.app.services.crypto import CryptoContext


def get_month_bounds(year: int, month: int) -> tuple[str, str]:
    """Return inclusive month start and exclusive next-month start (ISO date)."""

    month_start = date(year, month, 1)
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    return month_start.isoformat(), next_month_start.isoformat()


def cached_tag_name(
    ctx: CryptoContext,
    tag_hash: bytes,
    name_nonce: bytes,
    name_ct: bytes,
    alg: bytes,
) -> str:
    """Decrypt and cache tag names by hash."""

    plaintext = ctx.decrypt_entry(tag_hash.hex(), name_nonce, name_ct, alg)
    raw = (plaintext or "").strip()
    if not raw:
        return ""
    try:
        return canonicalize(raw)
    except ValueError:
        return ""
