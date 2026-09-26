"""Navigation and browser history: every view change is a real history entry.

htmx owns history. Navigations push the canonical URL (``/d/<date>``,
``/t/<tag>``), Back/Forward re-render from the server, and the address bar
always matches what is on screen.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from harness import ApiClient, marker, today_utc, wait_for_app, wait_for_htmx_idle


def day_url(day: date | str) -> re.Pattern[str]:
    value = day if isinstance(day, str) else day.isoformat()
    return re.compile(rf"/d/{re.escape(value)}$")


def open_day(page: Page, day: date | str) -> None:
    value = day if isinstance(day, str) else day.isoformat()
    page.goto(f"/d/{value}")
    wait_for_app(page)


def expect_day(page: Page, day: date) -> None:
    """The URL, the rendered entries and the header all show ``day``."""
    wait_for_htmx_idle(page)
    expect(page.locator("#entries")).to_have_attribute("data-date", day.isoformat())
    expect(page.locator("#main-content")).to_have_attribute("data-view", "diary")


def seeded_day(api: ApiClient, days_ago: int) -> tuple[date, str]:
    day = today_utc() - timedelta(days=days_ago)
    text = f"Seeded for navigation {marker()}"
    api.create_entry(day, text)
    return day, text


def test_previous_and_next_day_are_history_entries(
    app_page: Page, api: ApiClient
) -> None:
    today = today_utc()
    yesterday = today - timedelta(days=1)
    seeded_day(api, 2)  # guarantees there is history before today
    open_day(app_page, "today")

    app_page.locator("#prev-day").click()
    expect(app_page).to_have_url(day_url(yesterday))
    expect_day(app_page, yesterday)

    app_page.go_back()
    expect(app_page).to_have_url(day_url("today"))
    expect_day(app_page, today)

    app_page.go_forward()
    expect(app_page).to_have_url(day_url(yesterday))
    expect_day(app_page, yesterday)

    app_page.locator("#next-day").click()
    expect(app_page).to_have_url(day_url(today))
    expect_day(app_page, today)


def test_reload_renders_the_day_in_the_address_bar(
    app_page: Page, api: ApiClient
) -> None:
    day, text = seeded_day(api, 6)
    open_day(app_page, day)

    app_page.reload()
    wait_for_app(app_page)

    expect_day(app_page, day)
    expect(app_page.locator("#entries .entry.user", has_text=text)).to_be_visible()


def test_search_result_on_another_day_is_a_history_entry(
    app_page: Page, api: ApiClient
) -> None:
    day, text = seeded_day(api, 4)
    token = text.split()[-1]
    open_day(app_page, "today")

    # Type like a user: the search form triggers on keyup, not on input.
    app_page.locator("#search-input").press_sequentially(token)
    result = app_page.locator(f'#search-results a[data-date="{day.isoformat()}"]')
    expect(result).to_be_visible(timeout=20_000)  # indexing is asynchronous
    result.click()

    # The canonical URL is pushed; the scroll target is not left in it.
    expect(app_page).to_have_url(day_url(day))
    expect_day(app_page, day)
    expect(app_page.locator("#entries .entry.user", has_text=text)).to_be_in_viewport()

    app_page.go_back()
    expect(app_page).to_have_url(day_url("today"))
    expect_day(app_page, today_utc())

    app_page.go_forward()
    expect(app_page).to_have_url(day_url(day))
    expect_day(app_page, day)
    expect(app_page.locator("#entries .entry.user", has_text=text)).to_be_visible()


def test_logo_returns_to_today_as_a_history_entry(
    app_page: Page, api: ApiClient
) -> None:
    day, _ = seeded_day(api, 3)
    open_day(app_page, day)

    app_page.get_by_role("link", name="Return to today").click()
    expect(app_page).to_have_url(day_url("today"))
    expect_day(app_page, today_utc())

    app_page.go_back()
    expect(app_page).to_have_url(day_url(day))
    expect_day(app_page, day)


def test_logo_on_today_does_not_stack_duplicate_entries(app_page: Page) -> None:
    before = app_page.evaluate("history.length")

    app_page.get_by_role("link", name="Return to today").click()
    wait_for_htmx_idle(app_page)

    expect(app_page).to_have_url(day_url("today"))
    assert app_page.evaluate("history.length") == before


def test_calendar_day_is_a_history_entry(app_page: Page, api: ApiClient) -> None:
    today = today_utc()
    if today.day == 1:
        pytest.skip("no earlier day in the current calendar month")
    day, _ = seeded_day(api, 1)
    open_day(app_page, "today")

    app_page.locator("#calendar-btn").click()
    popover = app_page.locator("#calendar-popover")
    expect(popover).to_be_visible()
    popover.locator(f'td[data-date="{day.isoformat()}"] a').click()

    expect(app_page).to_have_url(day_url(day))
    expect_day(app_page, day)

    app_page.go_back()
    expect(app_page).to_have_url(day_url("today"))
    expect_day(app_page, today)


def test_switching_between_diary_and_traces(app_page: Page) -> None:
    toggle = app_page.locator("#view-mode-toggle")

    toggle.click()
    app_page.get_by_role("menuitem", name="Traces").click()
    expect(app_page).to_have_url(re.compile(r"/t(/|\?|$)"))
    expect(app_page.locator("#main-content")).to_have_attribute("data-view", "tags")
    expect(toggle).to_have_class(re.compile(r"\bview-mode-tags\b"))

    app_page.go_back()
    expect(app_page).to_have_url(day_url("today"))
    expect(app_page.locator("#main-content")).to_have_attribute("data-view", "diary")
    expect(toggle).to_have_class(re.compile(r"\bview-mode-diary\b"))
    expect(app_page.locator("#entries")).to_be_visible()

    app_page.go_forward()
    expect(app_page.locator("#main-content")).to_have_attribute("data-view", "tags")
    expect(toggle).to_have_class(re.compile(r"\bview-mode-tags\b"))
