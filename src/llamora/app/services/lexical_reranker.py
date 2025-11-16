import logging
import re
from typing import List

import ahocorasick

from llamora.settings import settings


TOKEN_PATTERN = re.compile(r"\w+")

logger = logging.getLogger(__name__)


class LexicalReranker:
    """Builds snippets and reranks vector candidates using lexical cues."""

    def rerank(
        self,
        query: str,
        candidates: List[dict],
        limit: int,
        tag_boosts: dict[str, float] | None = None,
    ) -> List[dict]:
        if not candidates:
            logger.debug("Lexical reranker received no candidates for query %r", query)
            return []
        lower_query = query.lower()
        automaton = ahocorasick.Automaton()
        automaton.add_word(lower_query, ("E", lower_query))
        tokens = [
            t for t in dict.fromkeys(TOKEN_PATTERN.findall(lower_query)) if len(t) >= 2
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

            snippet = self._build_snippet(content, merged)

            cosine = cand["cosine"]
            poor = cosine < float(settings.SEARCH.progressive.poor_match_max_cos)
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
                    "created_date": cand.get("created_date"),
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
        logger.debug("Lexical reranker returning %d results", len(results[:limit]))
        return results[:limit]

    def _build_snippet(self, content: str, spans: List[dict]) -> dict:
        max_len = 500
        context = 30
        if spans:
            first = spans[0]
            snippet_start = max(first["start"] - context, 0)
        else:
            snippet_start = 0
        snippet_end = min(snippet_start + max_len, len(content))
        leading_ellipsis = snippet_start > 0
        trailing_ellipsis = snippet_end < len(content)

        snippet_spans = []
        for m in spans:
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

        return {
            "segments": segments,
            "leading_ellipsis": leading_ellipsis,
            "trailing_ellipsis": trailing_ellipsis,
        }
