import logging
import time
import re
from typing import List
import hashlib

import numpy as np
import ahocorasick

from config import (
    MAX_SEARCH_QUERY_LENGTH,
    PROGRESSIVE_BATCH,
    PROGRESSIVE_K1,
    PROGRESSIVE_K2,
    PROGRESSIVE_MAX_MS,
    PROGRESSIVE_ROUNDS,
    POOR_MATCH_MAX_COS,
    POOR_MATCH_MIN_HITS,
)
from app.embed.model import async_embed_texts
from app.index.message_ann import MessageIndexRegistry


logger = logging.getLogger(__name__)


class SearchAPI:
    """High level search interface operating on encrypted messages."""

    def __init__(self, db):
        self.db = db
        self.registry = MessageIndexRegistry(db)

    async def knn_search(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int = PROGRESSIVE_K1,
        k2: int = PROGRESSIVE_K2,
    ) -> List[dict]:
        """Return KNN results with decrypted content and cosine scores."""
        logger.debug(
            "KNN search requested by user %s with k1=%d k2=%d", user_id, k1, k2
        )
        index = await self.registry.get_or_build(user_id, dek)
        q_vec = (await async_embed_texts([query])).astype(np.float32).reshape(1, -1)

        def quality(ids: List[str], cosines: List[float]) -> bool:
            if len(ids) < k2:
                return False
            max_cos = max(cosines) if cosines else 0.0
            hits = sum(c >= POOR_MATCH_MAX_COS for c in cosines)
            if max_cos < POOR_MATCH_MAX_COS or hits < POOR_MATCH_MIN_HITS:
                return False
            return True

        current_k1 = k1
        start = time.monotonic()
        ids, dists = index.search(q_vec, current_k1)
        cosines = [1 - d for d in dists]
        logger.debug("Initial search found %d candidates", len(ids))

        rounds = 0
        while not quality(ids, cosines):
            elapsed_ms = (time.monotonic() - start) * 1000
            if rounds >= PROGRESSIVE_ROUNDS or elapsed_ms >= PROGRESSIVE_MAX_MS:
                logger.debug(
                    "Stopping after %d rounds, elapsed %.1fms", rounds, elapsed_ms
                )
                break
            added = await self.registry.expand_older(user_id, dek, PROGRESSIVE_BATCH)
            logger.debug("Round %d added %d items", rounds + 1, added)
            if added <= 0:
                break
            rounds += 1
            if rounds == 1:
                current_k1 = min(2 * current_k1, 512)
            ids, dists = index.search(q_vec, current_k1)
            cosines = [1 - d for d in dists]

        seen = set()
        dedup_ids: List[str] = []
        id_cos: dict[str, float] = {}
        for mid, cos in zip(ids, cosines):
            if mid not in seen:
                seen.add(mid)
                dedup_ids.append(mid)
            if mid not in id_cos:
                id_cos[mid] = cos

        rows = await self.db.messages.get_messages_by_ids(user_id, dedup_ids, dek)
        row_map = {r["id"]: r for r in rows}

        results: List[dict] = []
        for mid in dedup_ids:
            row = row_map.get(mid)
            if not row:
                continue
            content = row.get("message", "")
            results.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "content": content,
                    "cosine": id_cos.get(mid, 0.0),
                }
            )

        results.sort(key=lambda r: r["cosine"], reverse=True)
        logger.debug("KNN search returning %d candidates", len(results))
        return results

    def _lexical_rerank(
        self,
        query: str,
        candidates: List[dict],
        limit: int,
        tag_boosts: dict[str, float] | None = None,
    ) -> List[dict]:
        lower_query = query.lower()
        automaton = ahocorasick.Automaton()
        automaton.add_word(lower_query, ("E", lower_query))
        tokens = [
            t for t in dict.fromkeys(re.findall(r"\w+", lower_query)) if len(t) >= 2
        ]
        for tok in tokens:
            automaton.add_word(tok, ("T", tok))
        automaton.make_automaton()
        token_count = len(tokens)

        results: List[dict] = []
        for cand in candidates:
            content = cand["content"]
            text_lower = content.lower()
            spans = []
            matched_tokens = set()
            exact = False
            for end, (kind, word) in automaton.iter(text_lower):
                start = end - len(word) + 1
                spans.append({"start": start, "end": end + 1, "kind": kind})
                if kind == "T":
                    matched_tokens.add(word)
                else:
                    exact = True

            spans.sort(key=lambda s: s["start"])
            merged: List[dict] = []
            for s in spans:
                if not merged or s["start"] > merged[-1]["end"]:
                    merged.append(s.copy())
                else:
                    m = merged[-1]
                    m["end"] = max(m["end"], s["end"])
                    if s["kind"] == "E" or m["kind"] == "E":
                        m["kind"] = "E"
            for m in merged:
                if text_lower[m["start"] : m["end"]] == lower_query:
                    m["kind"] = "E"

            overlap = len(matched_tokens) / token_count if token_count else 0.0
            boost = tag_boosts.get(cand["id"], 0.0) if tag_boosts else 0.0

            max_len = 500
            context = 30
            if merged:
                first = merged[0]
                snippet_start = max(first["start"] - context, 0)
            else:
                snippet_start = 0
            snippet_end = min(snippet_start + max_len, len(content))
            leading_ellipsis = snippet_start > 0
            trailing_ellipsis = snippet_end < len(content)

            snippet_spans = []
            for m in merged:
                if m["end"] <= snippet_start or m["start"] >= snippet_end:
                    continue
                snippet_spans.append(
                    {
                        "start": max(m["start"], snippet_start) - snippet_start,
                        "end": min(m["end"], snippet_end) - snippet_start,
                        "kind": m["kind"],
                    }
                )
            snippet_spans.sort(key=lambda s: s["start"])

            segments = []
            cursor = 0
            for sp in snippet_spans:
                if sp["start"] > cursor:
                    segments.append(
                        {
                            "text": content[
                                snippet_start + cursor : snippet_start + sp["start"]
                            ],
                            "hit": False,
                            "kind": None,
                        }
                    )
                segments.append(
                    {
                        "text": content[
                            snippet_start + sp["start"] : snippet_start + sp["end"]
                        ],
                        "hit": True,
                        "kind": "exact" if sp["kind"] == "E" else "token",
                    }
                )
                cursor = sp["end"]
            if cursor < snippet_end - snippet_start:
                segments.append(
                    {
                        "text": content[snippet_start + cursor : snippet_end],
                        "hit": False,
                        "kind": None,
                    }
                )

            snippet = {
                "segments": segments,
                "leading_ellipsis": leading_ellipsis,
                "trailing_ellipsis": trailing_ellipsis,
            }

            cosine = cand["cosine"]
            poor = cosine < POOR_MATCH_MAX_COS
            status = (
                "exact"
                if exact
                else ("token" if overlap > 0 else ("tag" if boost > 0 else "semantic"))
            )
            css_class = f"search-result-item status-{status}"
            if poor:
                css_class += " status-poor"
            sort_key = (
                2 if exact else (1 if overlap > 0 else 0),
                overlap + boost,
                cosine,
            )
            results.append(
                {
                    "id": cand["id"],
                    "created_at": cand["created_at"],
                    "role": cand["role"],
                    "snippet": snippet,
                    "status": status,
                    "css_class": css_class,
                    "_sort": sort_key,
                }
            )

        results.sort(key=lambda r: r["_sort"], reverse=True)
        for r in results:
            r.pop("_sort", None)
        return results[:limit]

    async def search(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int = PROGRESSIVE_K1,
        k2: int = PROGRESSIVE_K2,
    ):
        normalized = (query or "").strip()
        if not normalized:
            logger.info("Rejecting empty search query for user %s", user_id)
            return []
        if len(normalized) > MAX_SEARCH_QUERY_LENGTH:
            logger.info(
                "Rejecting overlong search query (len=%d, limit=%d) for user %s",
                len(normalized),
                MAX_SEARCH_QUERY_LENGTH,
                user_id,
            )
            return []

        logger.debug("Search requested by user %s with k1=%d k2=%d", user_id, k1, k2)
        query = normalized
        candidates = await self.knn_search(user_id, dek, query, k1, k2)
        tokens = [t for t in dict.fromkeys(re.findall(r"\S+", query)) if t]
        boosts: dict[str, float] = {}
        if tokens:
            tag_hashes = [
                hashlib.sha256(f"{user_id}:{t}".encode("utf-8")).digest()
                for t in tokens
            ]
            message_ids = [c["id"] for c in candidates]
            tag_map = await self.db.tags.get_messages_with_tag_hashes(
                user_id, tag_hashes, message_ids
            )
            for mid, hashes in tag_map.items():
                boosts[mid] = 0.1 * len(hashes)
        results = self._lexical_rerank(query, candidates, k2, boosts)
        logger.debug("Returning %d results for user %s", len(results), user_id)
        return results

    async def on_message_appended(
        self, user_id: str, msg_id: str, content: str, dek: bytes
    ):
        logger.debug(
            "Appending message %s for user %s",
            msg_id,
            user_id,
        )
        vec = (await async_embed_texts([content])).astype(np.float32).reshape(1, -1)
        await self.db.vectors.store_vector(msg_id, user_id, vec[0], dek)
        index = await self.registry.get_or_build(user_id, dek)
        if not index.contains(msg_id):
            index.add_batch([msg_id], vec)

    async def maintenance_tick(self) -> None:
        logger.debug("Running maintenance tick")
        self.registry.evict_idle()
