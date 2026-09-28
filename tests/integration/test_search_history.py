"""Recent searches: stored encrypted, newest first, one per query, per user."""

from __future__ import annotations

import pytest

from llamora.persistence.local_db import LocalDB
from llamora.settings import settings
from storage import Person

pytestmark = pytest.mark.anyio


async def recent(db: LocalDB, person: Person, limit: int = 20) -> list[str]:
    return await db.search_history.get_recent_searches(person.ctx(), limit)


async def test_newest_first_even_within_a_second(db: LocalDB, alice: Person) -> None:
    # Refining a query takes well under a second.
    for query in ("riv", "river", "river stones"):
        await db.search_history.record_search(alice.ctx(), query)

    assert await recent(db, alice) == ["river stones", "river", "riv"]


async def test_searching_again_moves_it_to_the_front(
    db: LocalDB, alice: Person
) -> None:
    for query in ("heron", "otter", "wren", "Heron"):
        await db.search_history.record_search(alice.ctx(), query)

    # One entry per query, whatever the case; the latest spelling is kept.
    assert await recent(db, alice) == ["Heron", "wren", "otter"]


async def test_blank_queries_are_not_recorded(db: LocalDB, alice: Person) -> None:
    await db.search_history.record_search(alice.ctx(), "   ")
    assert await recent(db, alice) == []


async def test_only_the_most_recent_are_kept(
    db: LocalDB, alice: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(settings.SEARCH, "recent_limit", 3)
    for i in range(6):
        await db.search_history.record_search(alice.ctx(), f"query {i}")

    assert await recent(db, alice) == ["query 5", "query 4", "query 3"]


async def test_each_user_sees_only_their_own(
    db: LocalDB, alice: Person, bob: Person
) -> None:
    await db.search_history.record_search(alice.ctx(), "alice's secret")
    await db.search_history.record_search(bob.ctx(), "bob's walk")

    assert await recent(db, alice) == ["alice's secret"]
    assert await recent(db, bob) == ["bob's walk"]


async def test_stored_encrypted(db: LocalDB, alice: Person) -> None:
    await db.search_history.record_search(alice.ctx(), "a very private query")

    async with db.pool.connection() as conn:
        cursor = await conn.execute("SELECT * FROM search_history")
        rows = [dict(r) for r in await cursor.fetchall()]
    assert len(rows) == 1
    stored = b"".join(
        v if isinstance(v, bytes) else str(v).encode() for v in rows[0].values()
    )
    assert b"private" not in stored


async def test_the_wrong_key_reads_nothing(db: LocalDB, alice: Person) -> None:
    await db.search_history.record_search(alice.ctx(), "a very private query")
    assert (
        await db.search_history.get_recent_searches(alice.ctx(dek=bytes(32)), 10) == []
    )
