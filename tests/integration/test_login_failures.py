"""The login failure counter behind the lockout (a TTL store counter)."""

from __future__ import annotations

import time

import pytest

from llamora.app.db.login_failures import LoginFailuresRepository
from llamora.app.db.ttl_store import TTLStore
from llamora.persistence.local_db import LocalDB

pytestmark = pytest.mark.anyio


def failures(db: LocalDB, ttl: int = 60) -> LoginFailuresRepository:
    return LoginFailuresRepository(TTLStore(db.pool), ttl=ttl)


async def test_failures_count_up(db: LocalDB) -> None:
    counter = failures(db)
    assert await counter.get_attempts("alice:127.0.0.1") == 0
    for expected in range(1, 7):
        assert await counter.record_failure("alice:127.0.0.1") == expected
        # Reading back works at every count (it used to break from the second).
        assert await counter.get_attempts("alice:127.0.0.1") == expected


async def test_counts_are_per_key_and_clear(db: LocalDB) -> None:
    counter = failures(db)
    await counter.record_failure("alice:127.0.0.1")
    await counter.record_failure("alice:127.0.0.1")
    await counter.record_failure("bob:127.0.0.1")

    assert await counter.get_attempts("bob:127.0.0.1") == 1
    await counter.clear("alice:127.0.0.1")
    assert await counter.get_attempts("alice:127.0.0.1") == 0
    assert await counter.get_attempts("bob:127.0.0.1") == 1


async def test_an_expired_count_starts_over(db: LocalDB) -> None:
    counter = failures(db, ttl=1)
    await counter.record_failure("alice:127.0.0.1")
    await counter.record_failure("alice:127.0.0.1")
    time.sleep(2.1)

    assert await counter.get_attempts("alice:127.0.0.1") == 0
    assert await counter.record_failure("alice:127.0.0.1") == 1
