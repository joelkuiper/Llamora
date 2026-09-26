"""Hover behaviour: tooltips, calendar day summaries and heatmap previews."""

from __future__ import annotations

from datetime import timedelta

import pytest
from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import ApiClient, marker, today_utc, wait_for_app, wait_for_htmx_idle


def move_away(page: Page) -> None:
    page.mouse.move(2, page.viewport_size["height"] - 2 if page.viewport_size else 600)


def day_summary_requests(fake_llm: FakeLLM) -> int:
    """Model calls for a calendar day summary (its schema bounds the length)."""

    def is_day_summary(request: dict) -> bool:
        schema = request.get("json_schema") or {}
        field = (schema.get("properties") or {}).get("summary") or {}
        return field.get("minLength") == 24 and field.get("maxLength") == 360

    return sum(1 for r in fake_llm.chat_requests(structured=True) if is_day_summary(r))


def test_tooltip_shows_on_hover_and_hides_on_leave(app_page: Page) -> None:
    tooltip = app_page.locator(".tooltip")

    app_page.locator("#calendar-btn").hover()
    expect(tooltip).to_be_visible()
    expect(tooltip).to_have_text("Change day")

    move_away(app_page)
    expect(tooltip).to_be_hidden()


def test_tooltip_is_dismissed_by_clicking_and_navigating(app_page: Page) -> None:
    tooltip = app_page.locator(".tooltip")
    logo = app_page.get_by_role("link", name="Return to today")

    logo.hover()
    expect(tooltip).to_have_text("Return to today")
    logo.click()

    expect(tooltip).to_be_hidden()
    wait_for_htmx_idle(app_page)
    expect(tooltip).to_be_hidden()


def test_calendar_day_hover_shows_a_cached_summary(
    fresh_session: tuple[Page, ApiClient], fake_llm: FakeLLM
) -> None:
    page, api = fresh_session
    today = today_utc()
    if today.day == 1:
        pytest.skip("yesterday is in the previous calendar month")
    yesterday = today - timedelta(days=1)
    api.create_entry(yesterday, f"A day worth summarising {marker()}")
    fake_llm.summary = f"A quiet day of walking and writing, {marker('summary')}."
    page.goto("/d/today")
    wait_for_app(page)

    page.locator("#calendar-btn").click()
    popover = page.locator("#calendar-popover")
    expect(popover.locator("#calendar")).to_be_visible()
    cell = popover.locator(f'td[data-date="{yesterday.isoformat()}"]')
    summary = page.locator(".calendar-day-tooltip:not(.heatmap-day-tooltip)")

    cell.hover()
    expect(summary).to_be_visible()
    expect(summary).to_contain_text(fake_llm.summary)
    generated = day_summary_requests(fake_llm)
    assert generated == 1

    popover.locator(".calendar-month-year").hover()  # leave the day
    expect(summary).to_be_hidden()

    cell.hover()
    expect(summary).to_contain_text(fake_llm.summary)
    assert day_summary_requests(fake_llm) == generated, "summary was regenerated"


def test_heatmap_hover_previews_the_day(
    fresh_session: tuple[Page, ApiClient], fake_llm: FakeLLM
) -> None:
    page, api = fresh_session
    tag = marker("walks")
    day = today_utc() - timedelta(days=3)
    entry_id = api.create_entry(day, f"Long walk by the river {marker()}")
    api.add_tag(entry_id, tag)
    fake_llm.summary = f"Out by the river most of the day, {marker('river')}."

    page.goto(f"/t/{tag}")
    wait_for_app(page)
    cell = page.locator(
        f'.activity-heatmap__cell[data-heatmap-date="{day.isoformat()}"]'
    )
    expect(cell).to_be_visible()

    cell.hover()

    preview = page.locator(".heatmap-day-tooltip")
    expect(preview).to_be_visible()
    expect(preview.locator(".heatmap-day-tooltip__summary")).to_contain_text(
        fake_llm.summary
    )
    expect(preview.locator(".heatmap-day-tooltip__date")).not_to_be_empty()

    move_away(page)
    expect(preview).to_be_hidden()
