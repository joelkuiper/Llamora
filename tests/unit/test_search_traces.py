"""Search and traces: query words that name a trace bring in (and lift) the
entries carrying it; plus the query normalizer's limits."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import pytest

from llamora.app.services.search_config import SearchConfig
from llamora.app.services.search_pipeline import DefaultSearchNormalizer
from llamora.app.services.search_pipeline.exceptions import InvalidSearchQuery
from llamora.app.services.search_pipeline.tag_enricher import DefaultTagEnricher
from llamora.app.util.tags import canonicalize, tag_hash
from llamora.settings import settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Ctx:
    user_id: str = "alice"


class Tags:
    def canonicalize(self, raw: str) -> str:
        return canonicalize(raw)


@dataclass
class FakeTagsRepo:
    """Traces as {canonical name: [entry ids, newest first]} for Alice."""

    traces: dict[str, list[str]]

    def _by_hash(self) -> dict[bytes, list[str]]:
        return {tag_hash("alice", name): ids for name, ids in self.traces.items()}

    async def get_recent_entries_for_tag_hashes(self, user_id, hashes, limit):
        found: list[str] = []
        for h in hashes:
            for entry_id in self._by_hash().get(h, []):
                if entry_id not in found:
                    found.append(entry_id)
        return found[:limit]

    async def get_tag_match_counts(self, user_id, hashes, entry_ids):
        by_hash = self._by_hash()
        return {e: sum(1 for h in hashes if e in by_hash.get(h, [])) for e in entry_ids}


@dataclass
class FakeDB:
    tags: FakeTagsRepo


@dataclass
class FakeIndexStore:
    texts: dict[str, str]
    hydrated: list[list[str]] = field(default_factory=list)

    async def hydrate_entries(self, ctx, ids):
        self.hydrated.append(list(ids))
        return [
            {
                "id": i,
                "text": self.texts[i],
                "created_at": "2026-09-01T12:00:00",
                "created_date": "2026-09-01",
                "role": "user",
            }
            for i in ids
            if i in self.texts
        ]


@dataclass
class FakeVectorSearch:
    index_store: FakeIndexStore


def enricher(
    traces: dict[str, list[str]], texts: dict[str, str] | None = None
) -> tuple[DefaultTagEnricher, FakeIndexStore]:
    store = FakeIndexStore(texts or {})
    return (
        DefaultTagEnricher(
            FakeDB(FakeTagsRepo(traces)),  # type: ignore[arg-type]
            Tags(),  # type: ignore[arg-type]
            FakeVectorSearch(store),  # type: ignore[arg-type]
        ),
        store,
    )


def candidate(entry_id: str) -> dict[str, Any]:
    return {"id": entry_id, "content": "", "cosine": 0.5}


# -- which words count as traces ------------------------------------------------


@pytest.mark.parametrize(
    ("query", "tokens"),
    [
        ("River", ["river"]),
        ("river RIVER river", ["river"]),
        ("golden hour", ["golden", "hour", "golden-hour"]),
        ("golden_hour", ["golden-hour"]),
        (":crescent_moon: night", ["🌙", "night"]),
        ("!!! river", ["river"]),
    ],
)
def test_query_words_become_trace_names(query: str, tokens: list[str]) -> None:
    subject, _ = enricher({})
    assert subject._tokenize(query) == tokens


# -- what traces do to the results ----------------------------------------------


async def test_entries_with_a_matching_trace_are_brought_in() -> None:
    subject, store = enricher(
        {"river": ["tagged"]}, texts={"tagged": "Nothing about water here."}
    )
    candidates: OrderedDict[str, dict] = OrderedDict(found=candidate("found"))

    result = await subject.enrich(Ctx(), "river", candidates, limit=10)  # type: ignore[arg-type]

    assert list(candidates) == ["found", "tagged"]
    assert candidates["tagged"]["content"] == "Nothing about water here."
    assert candidates["tagged"]["cosine"] == 0.0
    assert result.boosts == {"tagged": 1.0}
    assert store.hydrated == [["tagged"]]


async def test_more_matching_traces_lift_an_entry_more() -> None:
    subject, _ = enricher({"golden": ["both", "one"], "hour": ["both"]})
    candidates = OrderedDict(both=candidate("both"), one=candidate("one"))

    result = await subject.enrich(Ctx(), "golden hour", candidates, limit=10)  # type: ignore[arg-type]

    assert result.boosts["both"] > result.boosts["one"] == 1.0


async def test_another_users_traces_do_not_count() -> None:
    subject, _ = enricher({"river": ["tagged"]}, texts={"tagged": "x"})
    candidates: OrderedDict[str, dict] = OrderedDict()

    result = await subject.enrich(Ctx("bob"), "river", candidates, limit=10)  # type: ignore[arg-type]

    assert list(candidates) == [] and result.boosts == {}


async def test_no_trace_words_no_lookups() -> None:
    subject, store = enricher({"river": ["tagged"]})
    candidates: OrderedDict[str, dict] = OrderedDict()
    result = await subject.enrich(Ctx(), "!!! ???", candidates, limit=10)  # type: ignore[arg-type]
    assert result.tokens == [] and store.hydrated == []


# -- the normalizer ---------------------------------------------------------------


def normalizer() -> DefaultSearchNormalizer:
    return DefaultSearchNormalizer(SearchConfig.from_settings(settings))


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_empty_queries_are_refused(query: str) -> None:
    with pytest.raises(InvalidSearchQuery):
        normalizer().normalize("alice", query)


def test_queries_are_trimmed() -> None:
    result = normalizer().normalize("alice", "  river  ")
    assert (result.text, result.truncated) == ("river", False)


def test_long_queries_are_cut_to_the_limit() -> None:
    limit = int(settings.LIMITS.max_search_query_length)
    exact = normalizer().normalize("alice", "x" * limit)
    over = normalizer().normalize("alice", "x" * (limit + 1))
    assert not exact.truncated
    assert over.truncated and over.text == "x" * limit
