"""Unified summarization service with lockbox-backed caching and digest management."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

import orjson

from llamora.app.services.crypto import CryptoContext
from llamora.app.services.lockbox import Lockbox
from llamora.app.services.lockbox_store import LockboxStore
from llamora.app.services.digest_policy import entry_digest_aggregate, tag_digest

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SummaryPrompt:
    system: str
    user: str
    temperature: float = 0.2
    max_tokens: int = 220
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SummaryResult:
    text: str
    digest: str
    from_cache: bool


@dataclass(slots=True)
class SummarizeService:
    llm: Any
    store: LockboxStore
    lockbox: Lockbox
    entries_repo: Any
    tags_repo: Any

    @staticmethod
    def compute_digest(entry_digests: Iterable[str]) -> str:
        """Compute the canonical aggregate digest from individual entry digests."""

        return entry_digest_aggregate(entry_digests)

    async def generate(self, prompt: SummaryPrompt) -> str:
        """Call the LLM and extract the ``"summary"`` field from the JSON response."""
        messages = [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ]
        params: dict[str, Any] = {
            "temperature": prompt.temperature,
            "n_predict": prompt.max_tokens,
        }
        if prompt.response_format is not None:
            params["response_format"] = prompt.response_format

        raw = await self.llm.complete_messages(messages, params=params)
        return _extract_summary_field(raw)

    async def get_or_generate(
        self,
        ctx: CryptoContext,
        *,
        prompt: SummaryPrompt,
        cache_namespace: str,
        cache_key: str,
        digest: str,
        cache_field: str = "text",
    ) -> SummaryResult:
        """Full cache-check -> generate -> store pipeline."""
        cached_text = await self.get_cached(
            ctx, cache_namespace, cache_key, digest, field=cache_field
        )
        if cached_text is not None:
            return SummaryResult(text=cached_text, digest=digest, from_cache=True)

        text = await self.generate(prompt)
        await self.cache(
            ctx, cache_namespace, cache_key, digest, text, field=cache_field
        )
        return SummaryResult(text=text, digest=digest, from_cache=False)

    async def get_cached(
        self,
        ctx: CryptoContext,
        namespace: str,
        key: str,
        digest: str,
        *,
        field: str = "text",
    ) -> str | None:
        """Check lockbox for a cached summary matching the given digest."""
        cached = await self.store.get_json(ctx, namespace, key)
        if not isinstance(cached, dict):
            return None
        cached_digest = str(cached.get("digest") or "").strip()
        cached_value = cached.get(field)
        if cached_digest != digest or not isinstance(cached_value, str):
            return None
        cached_value = cached_value.strip()
        return cached_value or None

    async def cache(
        self,
        ctx: CryptoContext,
        namespace: str,
        key: str,
        digest: str,
        value: str,
        *,
        field: str = "text",
    ) -> None:
        """Store a summary in lockbox with its digest."""
        await self.store.set_json(ctx, namespace, key, {"digest": digest, field: value})

    # -- Digest cache methods ------------------------------------------------

    async def get_day_digest(self, ctx: CryptoContext, date: str) -> str:
        """Return the aggregate digest for a day, cached in lockbox."""
        cache_key = f"day:{date}"
        cached = await self.store.get_json(ctx, "digest", cache_key)
        if isinstance(cached, dict):
            value = cached.get("value")
            if isinstance(value, str) and value:
                return value

        digest = await self.entries_repo.get_day_summary_digest_for_date(
            ctx.user_id, date
        )
        await self.store.set_json(ctx, "digest", cache_key, {"value": digest})
        return digest

    async def get_tag_digest(self, ctx: CryptoContext, tag_hash: bytes) -> str:
        """Return the aggregate digest for a tag, cached in lockbox."""
        cache_key = f"tag:{tag_hash.hex()}"
        cached = await self.store.get_json(ctx, "digest", cache_key)
        if isinstance(cached, dict):
            value = cached.get("value")
            if isinstance(value, str) and value:
                return value

        entry_digests = await self.tags_repo.get_entry_digests_for_tag(
            ctx.user_id, tag_hash
        )
        digest = tag_digest(entry_digests)
        await self.store.set_json(ctx, "digest", cache_key, {"value": digest})
        return digest

    async def invalidate_day_digest(self, user_id: str, date: str) -> None:
        """Delete the cached day digest. No DEK needed."""
        await self.lockbox.delete(user_id, "digest", f"day:{date}")

    async def invalidate_tag_digest(self, user_id: str, tag_hash_hex: str) -> None:
        """Delete the cached tag digest. No DEK needed."""
        await self.lockbox.delete(user_id, "digest", f"tag:{tag_hash_hex}")


def _extract_summary_field(raw: str) -> str:
    if not raw:
        return ""
    try:
        parsed = orjson.loads(raw)
    except Exception:
        return ""
    if isinstance(parsed, dict):
        summary = parsed.get("summary")
        if isinstance(summary, str):
            return summary.strip()
    return ""


__all__ = ["SummarizeService", "SummaryPrompt", "SummaryResult"]
