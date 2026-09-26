"""Calendar popover: months, bounds, picking days, keyboard, month/year picker."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Locator, Page, expect

from harness import ApiClient, marker, today_utc, wait_for_app, wait_for_htmx_idle


def month_label(day: date) -> str:
    return f"{day.strftime('%B')} {day.year}"


def months_between(earlier: date, later: date) -> int:
    return (later.year * 12 + later.month) - (earlier.year * 12 + earlier.month)


def open_calendar(page: Page) -> Locator:
    page.locator("#calendar-btn").click()
    popover = page.locator("#calendar-popover")
    expect(popover).to_be_visible()
    expect(popover.locator("#calendar")).to_be_visible()
    wait_for_htmx_idle(page)
    return popover


def header(popover: Locator) -> Locator:
    return popover.locator(".calendar-month-year")


def day_cell(popover: Locator, day: date) -> Locator:
    return popover.locator(f'td[data-date="{day.isoformat()}"]')


def seed_old_entry(api: ApiClient, page: Page, days_ago: int = 40) -> date:
    """Seed the user's earliest entry; it sets how far back the calendar goes."""
    old = today_utc() - timedelta(days=days_ago)
    api.create_entry(old, f"Where it all started {marker()}")
    page.goto("/d/today")
    wait_for_app(page)
    return old


def test_months_are_bounded_by_first_entry_and_today(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    today = today_utc()
    first = seed_old_entry(api, page)

    popover = open_calendar(page)
    expect(header(popover)).to_have_text(month_label(today))
    expect(popover.get_by_role("button", name="Next month")).to_be_disabled()

    for _ in range(months_between(first, today)):
        popover.get_by_role("button", name="Previous month").click()
        wait_for_htmx_idle(page)
    expect(header(popover)).to_have_text(month_label(first))
    expect(popover.get_by_role("button", name="Previous month")).to_be_disabled()

    # Only days with entries (and today) are links.
    expect(day_cell(popover, first).locator("a")).to_have_count(1)
    other = first + timedelta(days=1 if first.day == 1 else -1)
    expect(day_cell(popover, other).locator("a")).to_have_count(0)


def test_picking_a_day_in_an_earlier_month(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    today = today_utc()
    first = seed_old_entry(api, page)

    popover = open_calendar(page)
    for _ in range(months_between(first, today)):
        popover.get_by_role("button", name="Previous month").click()
        wait_for_htmx_idle(page)
    day_cell(popover, first).locator("a").click()

    expect(page).to_have_url(re.compile(rf"/d/{first.isoformat()}$"))
    expect(popover).to_be_hidden()
    expect(page.locator("#entries")).to_have_attribute("data-date", first.isoformat())

    page.go_back()
    expect(page).to_have_url(re.compile(r"/d/today$"))
    expect(page.locator("#entries")).to_have_attribute("data-date", today.isoformat())


def test_month_year_picker_jumps_to_a_month(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    first = seed_old_entry(api, page, days_ago=70)

    popover = open_calendar(page)
    header(popover).click()
    picker = popover.locator("#calendar-picker")
    expect(picker).to_be_visible()

    picker.locator(f'[data-picker-year="{first.year}"]').click()
    picker.locator(f'[data-picker-month="{first.month}"]').click()
    footer = popover.locator("[data-calendar-footer]")
    expect(footer).to_contain_text(f"Set to {month_label(first)}")
    footer.click()

    expect(popover.locator("#calendar-picker")).to_have_count(0)
    expect(header(popover)).to_have_text(month_label(first))
    expect(day_cell(popover, first).locator("a")).to_have_count(1)


def test_keyboard_moves_between_days_and_opens_one(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    today = today_utc()
    if today.day == 1:
        pytest.skip("yesterday is in the previous month")
    yesterday = today - timedelta(days=1)
    api.create_entry(yesterday, f"Keyboard target {marker()}")
    page.goto("/d/today")
    wait_for_app(page)

    popover = open_calendar(page)
    active = popover.locator('[data-calendar-cell][tabindex="0"]')
    expect(active).to_have_attribute("data-date", today.isoformat())
    active.focus()
    page.keyboard.press("ArrowLeft")
    expect(day_cell(popover, yesterday)).to_be_focused()
    page.keyboard.press("Enter")

    expect(page).to_have_url(re.compile(rf"/d/{yesterday.isoformat()}$"))
    expect(page.locator("#entries")).to_have_attribute(
        "data-date", yesterday.isoformat()
    )


def test_today_button_returns_to_today(fresh_session: tuple[Page, ApiClient]) -> None:
    page, api = fresh_session
    first = seed_old_entry(api, page, days_ago=5)
    page.goto(f"/d/{first.isoformat()}")
    wait_for_app(page)

    popover = open_calendar(page)
    popover.get_by_role("button", name="Today").click()

    expect(page).to_have_url(re.compile(r"/d/today$"))
    expect(page.locator("#entries")).to_have_attribute(
        "data-date", today_utc().isoformat()
    )
    expect(popover).to_be_hidden()


def test_escape_closes_the_calendar(app_page: Page) -> None:
    popover = open_calendar(app_page)

    app_page.keyboard.press("Escape")

    expect(popover).to_be_hidden()
    expect(app_page.locator("#calendar-btn")).to_have_attribute(
        "aria-expanded", "false"
    )
