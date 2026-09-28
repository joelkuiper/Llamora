"""Paged search sessions (SearchStreamManager) with a fake vector index.

A session embeds its query once, remembers the candidates it has fetched and
the results it has shown, and pages on from there. Sessions belong to a user
and a query; anything else starts afresh.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from llamora.app.services import search_stream
from llamora.app.services.search_config import SearchConfig
from llamora.app.services.search_pipeline import (
    DefaultSearchNormalizer,
    SearchPipelineComponents,
)
from llamora.app.services.search_pipeline.reranker import DefaultSearchReranker
from llamora.app.services.search_pipeline.tag_enricher import TagEnrichment
from llamora.app.services.search_stream import SearchStreamManager
from llamora.settings import settings


@dataclass
class Ctx:
    user_id: str


@dataclass
class FakeIndexStore:
    def is_warming(self, user_id: str) -> bool:
        return False


@dataclass
class FakeVectorSearch:
    """Entries ranked by a fixed similarity; returns the top k2."""

    entries: list[dict[str, Any]]
    index_store: FakeIndexStore = field(default_factory=FakeIndexStore)
    calls: list[int] = field(default_factory=list)

    async def search_candidates(self, ctx, query, k1, k2, query_vec=None, **kwargs):
        self.calls.append(k2)
        ranked = sorted(self.entries, key=lambda e: e["cosine"], reverse=True)
        found = [dict(e) for e in ranked[:k2]]
        if kwargs.get("include_coverage"):
            return found, len(self.entries), {}
        return found, len(self.entries)


class NoTags:
    async def enrich(self, ctx, query, candidate_map, limit) -> TagEnrichment:
        return TagEnrichment(tokens=[], boosts={})


class FakeTagService:
    async def hydrate_search_results(self, ctx, results, tokens) -> None:
        return None


def entries(count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"e{i:03d}",
            "content": f"entry {i}",
            "created_at": "2026-09-01T12:00:00",
            "created_date": "2026-09-01",
            "role": "user",
            "cosine": 0.9 - i * 0.001,
        }
        for i in range(count)
    ]


@pytest.fixture
def embeds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_embed(texts):
        calls.extend(texts)
        return np.zeros((len(texts), 4), dtype=np.float32)

    monkeypatch.setattr(search_stream, "async_embed_texts", fake_embed)
    return calls


def manager(
    vector: FakeVectorSearch,
    *,
    k2: int | None = None,
    ttl: float = 900,
    max_sessions: int = 200,
    memory_budget: int = 0,
) -> SearchStreamManager:
    config = SearchConfig.from_settings(settings)
    if k2 is not None:
        config = dataclasses.replace(
            config, progressive=dataclasses.replace(config.progressive, k2=k2)
        )
    components = SearchPipelineComponents(
        normalizer=DefaultSearchNormalizer(config),
        candidate_generator=None,  # type: ignore[arg-type]  # unused by paging
        tag_enricher=NoTags(),  # type: ignore[arg-type]
        reranker=DefaultSearchReranker(),
    )
    return SearchStreamManager(
        vector_search=vector,  # type: ignore[arg-type]
        pipeline_components=components,
        config=config,
        stream_ttl=ttl,
        stream_max_sessions=max_sessions,
        tag_service=FakeTagService(),  # type: ignore[arg-type]
        stream_global_memory_budget_bytes=memory_budget,
    )


async def page(
    mgr: SearchStreamManager,
    session_id: str | None = None,
    *,
    user: str = "alice",
    query: str = "entry",
    limit: int = 10,
    window: int = 100,
):
    return await mgr.fetch_page(
        ctx=Ctx(user),  # type: ignore[arg-type]
        query=query,
        session_id=session_id,
        page_limit=limit,
        result_window=window,
    )


def ids(result) -> list[str]:
    return [r["id"] for r in result.results]


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# -- paging ---------------------------------------------------------------------


async def test_pages_follow_on_without_repeats(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(35)))

    first = await page(mgr, limit=15)
    second = await page(mgr, first.session_id)
    third = await page(mgr, first.session_id)

    assert first.session_id == second.session_id == third.session_id
    seen = ids(first) + ids(second) + ids(third)
    assert len(seen) == 35 and len(set(seen)) == 35
    assert [first.has_more, second.has_more, third.has_more] == [True, True, False]
    assert (third.showing_count, third.total_known) == (35, True)
    assert embeds == ["entry"]  # embedded once for the whole session


async def test_the_last_page_says_there_is_no_more(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(8)))
    result = await page(mgr, limit=15)
    assert len(result.results) == 8
    assert not result.has_more and result.total_known


async def test_results_stop_at_the_window(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(50)))
    first = await page(mgr, limit=10, window=15)
    second = await page(mgr, first.session_id, limit=10, window=15)
    assert len(first.results) == 10 and first.has_more
    assert len(second.results) == 5 and not second.has_more


async def test_every_result_is_reachable_whatever_k2_is(embeds: list[str]) -> None:
    # A k2 larger than the whole index fetches everything at once; the
    # results not shown yet must still be offered page by page.
    mgr = manager(FakeVectorSearch(entries(30)), k2=50)
    first = await page(mgr, limit=15)
    assert first.has_more, "15 of 30 shown, yet no more offered"
    second = await page(mgr, first.session_id, limit=15)
    assert len(set(ids(first) + ids(second))) == 30
    assert not second.has_more


async def test_a_query_is_trimmed_and_truncated(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(3)))
    limit = int(settings.LIMITS.max_search_query_length)
    result = await page(mgr, query="  " + "x" * (limit + 20) + "  ")
    assert result.truncated
    assert result.normalized_query == "x" * limit


# -- whose session --------------------------------------------------------------


async def test_another_users_session_id_starts_a_new_session(
    embeds: list[str],
) -> None:
    mgr = manager(FakeVectorSearch(entries(30)))
    alice = await page(mgr, user="alice", limit=15)

    bob = await page(mgr, alice.session_id, user="bob", limit=15)

    assert bob.session_id != alice.session_id
    assert ids(bob) == ids(alice)  # Bob's own first page, not Alice's second
    assert len(embeds) == 2


async def test_a_new_query_starts_a_new_session(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(30)))
    first = await page(mgr, query="entry", limit=15)
    other = await page(mgr, first.session_id, query="something else", limit=15)
    assert other.session_id != first.session_id
    assert other.showing_count == len(other.results)


# -- forgetting sessions --------------------------------------------------------


async def test_idle_sessions_are_forgotten(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(30)), ttl=60)
    first = await page(mgr, limit=15)
    mgr._sessions[first.session_id].last_access -= 120

    again = await page(mgr, first.session_id, limit=15)

    assert again.session_id != first.session_id
    assert ids(again) == ids(first)


async def test_only_the_most_recent_sessions_are_kept(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(30)), max_sessions=2)
    oldest = await page(mgr, query="one", limit=15)
    await page(mgr, query="two", limit=15)
    await page(mgr, query="three", limit=15)
    await page(mgr, query="four", limit=15)  # prunes down to the limit first

    assert oldest.session_id not in mgr._sessions
    # Pruned before the new session is added: at most one over, briefly.
    assert len(mgr._sessions) <= 2 + 1


async def test_sessions_are_evicted_over_the_memory_budget(embeds: list[str]) -> None:
    mgr = manager(FakeVectorSearch(entries(30)), memory_budget=1)
    first = await page(mgr, limit=15)
    await page(mgr, query="another", limit=15)

    assert first.session_id not in mgr._sessions
