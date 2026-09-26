"""Search: finding entries, opening results, keyboard use, paging, closing."""

from __future__ import annotations

import re
from datetime import timedelta

from playwright.sync_api import Locator, Page, expect

from harness import ApiClient, marker, today_utc, wait_for_app, wait_for_htmx_idle

INDEX_TIMEOUT = 20_000  # entries are indexed asynchronously after they are saved


def search(page: Page, query: str) -> Locator:
    """Type a query like a user (the form triggers on keyup); return result links."""
    field = page.locator("#search-input")
    field.fill("")
    field.press_sequentially(query)
    return page.locator("#search-results .search-results-list a[data-target]")


def result_for(page: Page, entry_id: str) -> Locator:
    return page.locator(f'#search-results a[data-target="entry-{entry_id}"]')


def test_search_finds_the_entry_and_marks_the_match(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    token = marker("heron")
    day = today_utc() - timedelta(days=3)
    entry_id = api.create_entry(day, f"Saw a {token} standing in the shallows")

    search(page, token)

    result = result_for(page, entry_id)
    expect(result).to_be_visible(timeout=INDEX_TIMEOUT)
    expect(result).to_have_attribute("data-date", day.isoformat())
    expect(result.locator("mark")).to_contain_text(token)


def test_same_day_result_scrolls_to_the_entry_without_navigating(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    token = marker("lantern")
    today = today_utc()
    for i in range(12):  # enough entries that the target starts off-screen
        api.create_entry(
            today, f"Filler entry number {i} {marker()}\n\n" + "line\n\n" * 6
        )
    target_id = api.create_entry(today, f"The {token} flickered by the window")
    page.goto("/d/today")
    wait_for_app(page)
    page.evaluate("window.scrollTo(0, 0)")
    history_before = page.evaluate("history.length")

    result = result_for(page, target_id)
    search(page, token)
    expect(result).to_be_visible(timeout=INDEX_TIMEOUT)
    result.click()

    expect(page.locator(f"#entry-{target_id}")).to_be_in_viewport()
    expect(page).to_have_url(re.compile(r"/d/today$"))
    assert page.evaluate("history.length") == history_before
    expect(page.locator("#search-results .sr-panel")).to_have_count(0)


def test_keyboard_opens_a_result(fresh_session: tuple[Page, ApiClient]) -> None:
    page, api = fresh_session
    token = marker("otter")
    day = today_utc() - timedelta(days=8)
    entry_id = api.create_entry(day, f"An {token} crossed the path")

    expect(search(page, token).first).to_be_visible(timeout=INDEX_TIMEOUT)
    field = page.locator("#search-input")
    field.press("ArrowDown")
    expect(result_for(page, entry_id)).to_be_focused()
    page.keyboard.press("Enter")

    expect(page).to_have_url(re.compile(rf"/d/{day.isoformat()}$"))
    expect(page.locator(f"#entry-{entry_id}")).to_be_in_viewport()


def test_escape_and_outside_click_close_the_results(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    token = marker("wren")
    api.create_entry(today_utc() - timedelta(days=2), f"A {token} sang at dawn")
    panel = page.locator("#search-results .sr-panel")

    expect(search(page, token).first).to_be_visible(timeout=INDEX_TIMEOUT)
    page.locator("#search-input").press("Escape")
    expect(panel).to_have_count(0)
    expect(page.locator("#search-input")).to_have_value("")

    expect(search(page, token).first).to_be_visible(timeout=INDEX_TIMEOUT)
    page.locator("#entries").click(position={"x": 5, "y": 5})
    expect(panel).to_have_count(0)


def test_more_results_load_while_scrolling(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    token = marker("pebble")
    total = 20  # the first page shows 15 (settings.SEARCH.initial_page_size)
    for i in range(total):
        api.create_entry(
            today_utc() - timedelta(days=i + 1), f"Found a {token} number {i}"
        )

    results = search(page, token)
    # Search is hybrid: semantic neighbours may follow the keyword matches.
    matches = results.filter(has=page.locator("mark"))
    expect(results).to_have_count(15, timeout=INDEX_TIMEOUT)
    expect(matches).to_have_count(15)

    results.last.scroll_into_view_if_needed()
    expect(matches).to_have_count(total)
    wait_for_htmx_idle(page)


def test_opening_the_search_url_directly_goes_to_the_diary(app_page: Page) -> None:
    # Search lives in the header's overlay; /search only serves its results.
    app_page.goto("/search?q=anything")

    expect(app_page).to_have_url(re.compile(r"/d/today$"))
    wait_for_app(app_page)
    expect(app_page.locator("#entries")).to_be_visible()
