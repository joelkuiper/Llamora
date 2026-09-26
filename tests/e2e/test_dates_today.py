"""What "today" is, and where entries land.

Intended behaviour: today is the writer's local calendar day as reported by
the browser; the server's own clock is only a fallback. Most tests pin the
browser months away from the server's real date, so any place that silently
falls back to the server's notion of today shows up.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import wait_for_app, wait_for_htmx_idle, write_entry
from timekit import (
    Diary,
    at,
    long_date,
    opening_requests,
    prompt_day,
    reply_requests,
)

DiaryAt = Callable[..., Diary]

# (zone, local wall time) pairs on 10 March 2026, far east and far west of UTC.
EAST = ("Pacific/Kiritimati", 7)  # UTC+14
WEST = ("Pacific/Pago_Pago", 20)  # UTC-11
TODAY = date(2026, 3, 10)


def day_url(day: date | str) -> re.Pattern[str]:
    value = day if isinstance(day, str) else day.isoformat()
    return re.compile(rf"/d/{re.escape(value)}(\?|$)")


def open_calendar(page: Page):
    page.locator("#calendar-btn").click()
    popover = page.locator("#calendar-popover")
    expect(popover.locator("#calendar")).to_be_visible()
    wait_for_htmx_idle(page)
    return popover


# --- what "today" is ---------------------------------------------------------


@pytest.mark.parametrize(
    ("tz", "hour"), [EAST, WEST, ("UTC", 12)], ids=["east", "west", "utc"]
)
def test_today_is_the_clients_local_date(diary_at: DiaryAt, tz: str, hour: int) -> None:
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))
    page = diary.page

    expect(page.locator("#entries")).to_have_attribute("data-date", TODAY.isoformat())
    expect(page.locator("#calendar-label")).to_have_text(long_date(TODAY))
    expect(page.locator("#entry-text")).to_be_enabled()
    expect(page.locator("#next-day")).to_be_disabled()


def test_reloading_today_keeps_the_clients_date(diary_at: DiaryAt) -> None:
    tz, hour = WEST
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))

    diary.page.goto("/d/today")
    wait_for_app(diary.page)

    expect(diary.page.locator("#entries")).to_have_attribute(
        "data-date", TODAY.isoformat()
    )


def test_correcting_today_does_not_trap_the_back_button(diary_at: DiaryAt) -> None:
    tz, hour = EAST
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))
    page = diary.page
    past = TODAY - timedelta(days=3)
    diary.api.create_entry(past, "Three days ago")

    page.goto(f"/d/{past.isoformat()}")
    wait_for_app(page)
    page.goto("/d/today")  # full load: the server can't know the client's date yet
    wait_for_app(page)
    expect(page.locator("#entries")).to_have_attribute("data-date", TODAY.isoformat())

    page.go_back()
    wait_for_app(page)
    expect(page).to_have_url(day_url(past))
    expect(page.locator("#entries")).to_have_attribute("data-date", past.isoformat())


def test_first_visit_without_a_timezone_cookie(diary_at: DiaryAt) -> None:
    # Server just past UTC midnight on the 11th; the writer is still on the 10th.
    tz = "America/Los_Angeles"
    moment = at("UTC", 2026, 3, 11, 0, 30)
    diary = diary_at(tz=tz, at=moment, server_now=moment)

    expect(diary.page.locator("#entries")).to_have_attribute("data-date", "2026-03-10")
    expect(diary.page.locator("#calendar-label")).to_have_text(
        long_date(date(2026, 3, 10))
    )


def test_calendar_follows_the_clients_today(diary_at: DiaryAt) -> None:
    tz, hour = WEST
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))

    popover = open_calendar(diary.page)

    expect(popover.locator(".calendar-month-year")).to_have_text("March 2026")
    expect(popover.locator("td.is-today")).to_have_attribute(
        "data-date", TODAY.isoformat()
    )
    expect(popover.get_by_role("button", name="Next month")).to_be_disabled()


def test_future_is_judged_by_the_clients_date(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    # 1 June is in the writer's future, though in the server's real past.
    tz, hour = WEST
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))
    before = len(opening_requests(fake_llm))

    diary.page.goto("/d/2026-06-01")
    wait_for_app(diary.page)

    expect(diary.page.get_by_text("This day has not arrived yet.")).to_be_visible()
    expect(diary.page.locator("#entry-form")).to_have_count(0)
    assert len(opening_requests(fake_llm)) == before


def test_tomorrow_is_the_future(diary_at: DiaryAt) -> None:
    tz, hour = EAST
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))

    diary.page.goto(f"/d/{(TODAY + timedelta(days=1)).isoformat()}")
    wait_for_app(diary.page)

    expect(diary.page.get_by_text("This day has not arrived yet.")).to_be_visible()
    expect(diary.page.locator("#entry-form")).to_have_count(0)


def test_previous_and_next_follow_the_clients_today(diary_at: DiaryAt) -> None:
    tz, hour = WEST
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))
    page = diary.page
    diary.api.create_entry(TODAY - timedelta(days=2), "An earlier day")
    page.goto("/d/today")
    wait_for_app(page)

    expect(page.locator("#prev-day")).to_have_attribute(
        "data-tooltip-title", "Yesterday"
    )
    expect(page.locator("#next-day")).to_be_disabled()

    page.locator("#prev-day").click()
    wait_for_htmx_idle(page)
    expect(page.locator("#entries")).to_have_attribute(
        "data-date", (TODAY - timedelta(days=1)).isoformat()
    )
    expect(page.locator("#next-day")).to_be_enabled()
    expect(page.locator("#entry-form")).to_have_count(0)


# --- where entries land --------------------------------------------------------


def test_late_evening_entry_stays_on_the_local_day(diary_at: DiaryAt) -> None:
    # 23:30 in Los Angeles is already 07:30 on the 11th in UTC.
    tz = "America/Los_Angeles"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, 23, 30))
    page = diary.page

    entry = write_entry(page, "Late night thoughts before bed")
    expect(entry.locator("time.entry-time")).to_have_text(re.compile(r"^23:3\d$"))

    page.reload()
    wait_for_app(page)
    expect(page.locator("#entries")).to_have_attribute("data-date", "2026-03-10")
    expect(page.locator("#entries .entry.user", has_text="Late night")).to_be_visible()
    popover = open_calendar(page)
    expect(popover.locator('td[data-date="2026-03-10"] a')).to_have_count(1)
    expect(popover.locator('td[data-date="2026-03-11"] a')).to_have_count(0)


def test_travelling_the_same_instant_lands_on_different_days(
    diary_at: DiaryAt, open_page: Callable[..., Page], tmp_path
) -> None:
    moment = at(
        "America/Los_Angeles", 2026, 3, 10, 23, 30
    )  # 16:30 on the 11th in Tokyo
    diary = diary_at(tz="America/Los_Angeles", at=moment)
    write_entry(diary.page, "Written in Los Angeles")

    state = tmp_path / "state.json"
    diary.page.context.storage_state(path=str(state))
    tokyo = open_page(
        base_url=diary.base_url, tz="Asia/Tokyo", at=moment, storage_state=str(state)
    )
    tokyo.goto("/d/today")
    wait_for_app(tokyo)

    expect(tokyo.locator("#entries")).to_have_attribute("data-date", "2026-03-11")
    expect(tokyo.locator("#entries .entry.user", has_text="Los Angeles")).to_have_count(
        0
    )
    tokyo.locator("#prev-day").click()
    wait_for_htmx_idle(tokyo)
    expect(
        tokyo.locator("#entries .entry.user", has_text="Los Angeles")
    ).to_be_visible()


def test_editing_is_limited_to_the_clients_today(diary_at: DiaryAt) -> None:
    tz, hour = EAST
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, hour))
    yesterday = TODAY - timedelta(days=1)
    entry_id = diary.api.create_entry(yesterday, "Yesterday's words")

    diary.page.goto(f"/d/{yesterday.isoformat()}")
    wait_for_app(diary.page)
    entry = diary.page.locator(f"#entry-{entry_id}")
    expect(entry.get_by_role("button", name="Edit entry")).to_be_disabled()

    # The server enforces the same rule, judged by the client's date.
    headers = {
        "X-Client-Today": TODAY.isoformat(),
        "X-Timezone": tz,
        "HX-Request": "true",
    }
    resp = diary.api._client.get(f"/e/entry/{entry_id}/edit", headers=headers)
    assert resp.status_code == 403
    resp = diary.api._client.get(
        f"/e/entry/{entry_id}/edit",
        headers={**headers, "X-Client-Today": yesterday.isoformat()},
    )
    assert resp.status_code == 200


def test_reply_prompt_uses_the_clients_local_time(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    tz = "America/Los_Angeles"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 10, 7, 15))
    entry = write_entry(diary.page, "Morning coffee on the porch")
    before = len(reply_requests(fake_llm))

    entry.get_by_role("button", name="Respond").click()
    wait_for_app(diary.page)

    requests = reply_requests(fake_llm)[before:]
    assert requests, "no reply was requested"
    assert prompt_day(requests[-1]) == (long_date(TODAY), "early-morning")


def test_reply_context_excludes_later_entries(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    diary = diary_at(tz="UTC", at=at("UTC", 2026, 3, 10, 9))
    first = write_entry(diary.page, "The first thought of the morning")
    write_entry(diary.page, "A later thought the reply must not see")
    before = len(reply_requests(fake_llm))

    first.get_by_role("button", name="Respond").click()
    wait_for_app(diary.page)

    request = reply_requests(fake_llm)[before:][-1]
    text = "\n".join(str(m.get("content", "")) for m in request["messages"])
    assert "The first thought of the morning" in text
    assert "must not see" not in text
