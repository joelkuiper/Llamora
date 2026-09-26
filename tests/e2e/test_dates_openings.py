"""Day openings: one per user per day, for the writer's today only.

Intended behaviour: the opening is generated the first time the writer's
today is opened, reflects their local date and part of day, recaps their
yesterday, and is never regenerated or created for any other date.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import marker, wait_for_app, wait_for_htmx_idle
from timekit import Diary, at, long_date, opening_requests, prompt_day, prompt_text

DiaryAt = Callable[..., Diary]
TODAY = date(2026, 3, 10)


def openings_on_page(page: Page):
    return page.locator("#entries .entry--opening")


def client_headers(diary: Diary, day: date) -> dict[str, str]:
    return {"X-Client-Today": day.isoformat(), "X-Timezone": diary.tz}


def test_first_visit_streams_one_opening_for_the_clients_day(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    fake_llm.reply = f"Welcome, {marker('hello')}."
    diary = diary_at(
        tz="Pacific/Kiritimati", at=at("Pacific/Kiritimati", 2026, 3, 10, 9)
    )

    expect(openings_on_page(diary.page)).to_have_count(1)
    expect(openings_on_page(diary.page)).to_contain_text(fake_llm.reply)
    requests = opening_requests(fake_llm)
    assert len(requests) == 1
    assert prompt_day(requests[0]) == (long_date(TODAY), "morning")
    assert "first-time user" in prompt_text(requests[0])


@pytest.mark.parametrize(
    ("hour", "part"),
    [
        (3, "night"),
        (6, "early-morning"),
        (9, "morning"),
        (14, "afternoon"),
        (19, "evening"),
        (22, "late-night"),
    ],
)
def test_opening_knows_the_clients_part_of_day(
    diary_at: DiaryAt, fake_llm: FakeLLM, hour: int, part: str
) -> None:
    tz = "America/Los_Angeles"
    diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))

    requests = opening_requests(fake_llm)
    assert len(requests) == 1
    assert prompt_day(requests[0]) == (long_date(TODAY), part)


def test_opening_is_not_regenerated_by_reloads_or_navigation(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    tz = "Europe/Amsterdam"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, 8))
    page = diary.page
    diary.api.create_entry(TODAY - timedelta(days=1), "Yesterday, for navigation")

    page.reload()
    wait_for_app(page)
    page.locator("#prev-day").click()
    wait_for_htmx_idle(page)
    page.locator("#next-day").click()
    wait_for_htmx_idle(page)
    page.goto("/d/today")
    wait_for_app(page)

    expect(openings_on_page(page)).to_have_count(1)
    assert len(opening_requests(fake_llm)) == 1


def test_no_opening_on_past_or_future_days(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    tz = "Europe/Amsterdam"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, 8))
    page = diary.page
    before = len(opening_requests(fake_llm))

    for day in (TODAY - timedelta(days=5), TODAY + timedelta(days=2)):
        page.goto(f"/d/{day.isoformat()}")
        wait_for_app(page)
        expect(openings_on_page(page)).to_have_count(0)

    assert len(opening_requests(fake_llm)) == before


def test_two_tabs_share_one_opening(
    diary_at: DiaryAt, open_page: Callable[..., Page], fake_llm: FakeLLM, tmp_path
) -> None:
    tz = "Europe/Amsterdam"
    moment = at(tz, 2026, 3, 10, 8)
    fake_llm.chunk_delay = 0.2
    fake_llm.reply = f"Good morning to both tabs {marker('tabs')} one two three four"
    diary = diary_at(tz=tz, at=moment, settle=False)
    expect(openings_on_page(diary.page)).to_contain_text("Good morning")

    state = tmp_path / "state.json"
    diary.page.context.storage_state(path=str(state))
    second = open_page(
        base_url=diary.base_url, tz=tz, at=moment, storage_state=str(state)
    )
    second.goto("/d/today")

    for page in (diary.page, second):
        expect(openings_on_page(page)).to_contain_text(fake_llm.reply, timeout=15_000)
        wait_for_app(page)
    assert len(opening_requests(fake_llm)) == 1

    second.reload()
    wait_for_app(second)
    expect(openings_on_page(second)).to_have_count(1)


def test_opening_route_refuses_other_days(diary_at: DiaryAt, fake_llm: FakeLLM) -> None:
    tz = "Europe/Amsterdam"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, 8))
    before = len(opening_requests(fake_llm))
    client = diary.api._client

    for day in (TODAY - timedelta(days=1), TODAY + timedelta(days=1)):
        resp = client.get(
            f"/e/opening/{day.isoformat()}", headers=client_headers(diary, TODAY)
        )
        assert resp.status_code in {200, 400, 404}

    assert len(opening_requests(fake_llm)) == before


def test_opening_route_returns_the_existing_opening(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    tz = "Europe/Amsterdam"
    fake_llm.reply = f"The one and only opening {marker('only')}"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, 8))
    assert len(opening_requests(fake_llm)) == 1

    resp = diary.api._client.get(
        f"/e/opening/{TODAY.isoformat()}", headers=client_headers(diary, TODAY)
    )

    assert len(opening_requests(fake_llm)) == 1, "a second opening was generated"
    assert fake_llm.reply.split()[-1] in resp.text


def test_opening_recaps_the_clients_yesterday(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    # A returning user: the opening for the 10th recaps what they wrote on the 9th.
    tz = "Pacific/Kiritimati"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 9, 20))
    diary.api.create_entry(date(2026, 3, 9), f"Planted tomatoes {marker('garden')}")
    diary.page.clock.set_system_time(at(tz, 2026, 3, 10, 8))
    before = len(opening_requests(fake_llm))

    diary.page.goto("/d/today")
    wait_for_app(diary.page)

    requests = opening_requests(fake_llm)[before:]
    assert len(requests) == 1
    assert prompt_day(requests[0]) == (long_date(TODAY), "morning")
    assert "Planted tomatoes" in prompt_text(requests[0])


def test_opening_after_a_quiet_yesterday(diary_at: DiaryAt, fake_llm: FakeLLM) -> None:
    tz = "America/Los_Angeles"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 5, 20))
    diary.api.create_entry(date(2026, 3, 5), "Some days ago")
    diary.page.clock.set_system_time(at(tz, 2026, 3, 10, 8))
    before = len(opening_requests(fake_llm))

    diary.page.goto("/d/today")
    wait_for_app(diary.page)

    requests = opening_requests(fake_llm)[before:]
    assert len(requests) == 1
    assert "no activity yesterday" in prompt_text(requests[0])
