"""Midnight: a page showing today moves to the new day.

Intended behaviour: at local midnight (or when the tab becomes visible again
after the clock passed it) a page showing today becomes the new day: a fresh
page with its own opening, at the same canonical URL, without extra history
entries, carrying any unsent draft along. Pages for other days stay put.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import marker, wait_for_app, wait_for_htmx_idle, write_entry
from timekit import Diary, at, long_date, opening_requests, prompt_day

DiaryAt = Callable[..., Diary]
TZ = "America/Los_Angeles"


def expect_day(page: Page, day: date) -> None:
    expect(page.locator("#entries")).to_have_attribute("data-date", day.isoformat())
    expect(page.locator("#calendar-label")).to_have_text(long_date(day))


def cross_midnight(page: Page, *, by: str = "00:01:00") -> None:
    """Advance the browser clock, firing timers (the midnight check among them)."""
    page.clock.fast_forward(by)
    wait_for_htmx_idle(page)
    wait_for_app(page)


def wake_up(page: Page, moment) -> None:
    """A laptop waking: the clock jumped without timers firing, then the tab shows."""
    page.clock.set_system_time(moment)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    wait_for_htmx_idle(page)
    wait_for_app(page)


def test_today_becomes_the_new_day_at_midnight(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 59, 30))
    page = diary.page
    history_before = page.evaluate("history.length")

    cross_midnight(page)

    expect_day(page, date(2026, 3, 11))
    expect(page).to_have_url(re.compile(r"/d/today$"))
    expect(page.locator("#entry-text")).to_be_enabled()
    assert page.evaluate("history.length") == history_before, "rollover added history"
    days = [prompt_day(r) for r in opening_requests(fake_llm)]
    assert (long_date(date(2026, 3, 11)), "night") in days


def test_waking_after_midnight_moves_to_the_new_day(diary_at: DiaryAt) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 30))

    wake_up(diary.page, at(TZ, 2026, 3, 11, 7, 15))

    expect_day(diary.page, date(2026, 3, 11))


def test_back_after_midnight_never_shows_a_writable_yesterday(
    diary_at: DiaryAt,
) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 59, 30))
    page = diary.page
    page.goto("/d/today")  # a second history entry to go back to
    wait_for_app(page)

    cross_midnight(page)
    page.go_back()
    wait_for_app(page)

    if page.locator("#entries").get_attribute("data-date") == "2026-03-10":
        expect(page.locator("#entry-form")).to_have_count(0)
    else:
        expect_day(page, date(2026, 3, 11))


def test_unsent_draft_moves_to_the_new_day(diary_at: DiaryAt) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 59, 30))
    page = diary.page
    draft = f"Still writing when the clock struck {marker()}"
    page.locator("#entry-text").fill(draft)

    cross_midnight(page)

    expect_day(page, date(2026, 3, 11))
    expect(page.locator("#entry-text")).to_have_value(draft)
    page.reload()
    wait_for_app(page)
    expect(page.locator("#entry-text")).to_have_value(draft)


def test_sending_from_a_stale_page_does_not_file_under_yesterday(
    diary_at: DiaryAt,
) -> None:
    # The clock passed midnight but no timer or visibility change noticed yet.
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 50))
    page = diary.page
    page.clock.set_system_time(at(TZ, 2026, 3, 11, 0, 5))
    text = f"Just after midnight {marker()}"

    textarea = page.locator("#entry-text")
    textarea.fill(text)
    textarea.press("Enter")
    wait_for_htmx_idle(page)
    wait_for_app(page)

    # Nothing written after midnight may appear on (or be saved to) the 10th.
    expect_day(page, date(2026, 3, 11))
    page.goto("/d/2026-03-10")
    wait_for_app(page)
    expect(page.locator("#entries .entry.user", has_text=text)).to_have_count(0)


def test_reply_streaming_across_midnight_stays_with_its_entry(
    diary_at: DiaryAt, fake_llm: FakeLLM
) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 59, 40))
    page = diary.page
    entry = write_entry(page, f"Late question {marker()}")
    entry_id = entry.get_attribute("data-entry-id")
    fake_llm.chunk_delay = 0.3
    fake_llm.reply = f"An answer that spans midnight {marker('span')} one two three"

    entry.get_by_role("button", name="Respond").click()
    expect(page.locator(f"#entry-responses-{entry_id}")).to_contain_text("An answer")
    cross_midnight(page, by="00:00:30")

    page.goto("/d/2026-03-10")
    wait_for_app(page)
    expect(page.locator(f"#entry-responses-{entry_id}")).to_contain_text(
        fake_llm.reply, timeout=15_000
    )


def test_past_day_pages_stay_put_at_midnight(diary_at: DiaryAt) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 59, 30))
    page = diary.page
    diary.api.create_entry(date(2026, 3, 8), "An older day")
    page.goto("/d/2026-03-08")
    wait_for_app(page)

    cross_midnight(page)

    expect_day(page, date(2026, 3, 8))
    expect(page).to_have_url(re.compile(r"/d/2026-03-08$"))


def test_new_year(diary_at: DiaryAt) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 12, 31, 23, 59, 30))
    page = diary.page

    cross_midnight(page)

    expect_day(page, date(2027, 1, 1))
    page.locator("#calendar-btn").click()
    popover = page.locator("#calendar-popover")
    expect(popover.locator(".calendar-month-year")).to_have_text("January 2027")
    expect(popover.get_by_role("button", name="Next month")).to_be_disabled()


def test_short_dst_day_rolls_over_at_local_midnight(diary_at: DiaryAt) -> None:
    # Europe/Amsterdam springs forward on 29 March 2026: a 23-hour day.
    tz = "Europe/Amsterdam"
    diary = diary_at(tz=tz, at=at(tz, 2026, 3, 29, 0, 0, 30))
    page = diary.page

    cross_midnight(page, by="22:58:00")  # 23:58:30 local: 02:00-03:00 never happens
    expect_day(page, date(2026, 3, 29))
    cross_midnight(page, by="00:02:00")
    expect_day(page, date(2026, 3, 30))


def test_long_dst_day_rolls_over_at_local_midnight(diary_at: DiaryAt) -> None:
    # Europe/Amsterdam falls back on 25 October 2026: a 25-hour day.
    tz = "Europe/Amsterdam"
    diary = diary_at(tz=tz, at=at(tz, 2026, 10, 25, 0, 0, 30))
    page = diary.page

    cross_midnight(page, by="24:00:00")  # 23:00:30 local: still the 25th
    expect_day(page, date(2026, 10, 25))
    cross_midnight(page, by="01:00:00")
    expect_day(page, date(2026, 10, 26))
