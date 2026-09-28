"""Search: finding entries, opening results, keyboard use, paging, closing."""

from __future__ import annotations

import re
from datetime import timedelta

from playwright.sync_api import Locator, Page, expect

from harness import (
    ApiClient,
    login,
    marker,
    today_utc,
    wait_for_app,
    wait_for_htmx_idle,
)

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


# -- the index keeps up -----------------------------------------------------------


def test_an_edited_entry_is_found_by_its_new_words(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    old, new = marker("marsh"), marker("meadow")
    entry_id = api.create_entry(today_utc() - timedelta(days=1), f"Walked the {old}")
    expect(search(page, old).first).to_be_visible(timeout=INDEX_TIMEOUT)

    api.update_entry(entry_id, f"Walked the {new}").raise_for_status()

    search(page, new)
    expect(result_for(page, entry_id).locator("mark")).to_contain_text(
        new, timeout=INDEX_TIMEOUT
    )
    search(page, old)
    expect(result_for(page, entry_id).locator("mark")).to_have_count(
        0, timeout=INDEX_TIMEOUT
    )


def test_a_deleted_entry_is_not_found(fresh_session: tuple[Page, ApiClient]) -> None:
    page, api = fresh_session
    token = marker("lichen")
    entry_id = api.create_entry(today_utc() - timedelta(days=1), f"Grey {token}")
    expect(search(page, token).first).to_be_visible(timeout=INDEX_TIMEOUT)

    api.delete_entry(entry_id).raise_for_status()

    search(page, "")
    search(page, token)
    expect(result_for(page, entry_id)).to_have_count(0, timeout=INDEX_TIMEOUT)


# -- traces -------------------------------------------------------------------------


def test_a_trace_finds_entries_that_never_mention_it(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    trace = marker("tidepool")
    entry_id = api.create_entry(today_utc() - timedelta(days=4), "A quiet walk.")
    api.add_tag(entry_id, trace)

    search(page, trace)

    result = result_for(page, entry_id)
    expect(result).to_be_visible(timeout=INDEX_TIMEOUT)
    expect(result.locator(".entry-tag.is-highlighted")).to_have_text(trace)


def test_an_emoji_shortcode_finds_the_traced_emoji(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    entry_id = api.create_entry(today_utc() - timedelta(days=4), "Heard it at dusk.")
    api.add_tag(entry_id, "🦉")

    search(page, ":owl:")

    result = result_for(page, entry_id)
    expect(result).to_be_visible(timeout=INDEX_TIMEOUT)
    expect(result.locator(".entry-tag.is-highlighted")).to_contain_text("🦉")


# -- highlighting -------------------------------------------------------------------


def test_a_long_query_highlights_whole_words_only(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    token = marker("gale")
    entry_id = api.create_entry(
        today_utc() - timedelta(days=1),
        f"I kept thinking that there is {token}, and this window rattles all night.",
    )

    search(page, f"the {token} at the window in the night")

    marks = result_for(page, entry_id).locator("mark")
    expect(marks.first).to_be_visible(timeout=INDEX_TIMEOUT)
    expect(marks).to_have_text([token, "window", "night"])


def test_a_query_over_the_limit_is_shortened_and_says_so(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    token = marker("heath")
    api.create_entry(today_utc() - timedelta(days=1), f"Out on the {token}")
    field = page.locator("#search-input")

    field.fill(token + " " + "x" * 600)
    field.press("Enter")

    notice = page.locator(".search-results-notice")
    expect(notice).to_contain_text("truncated to the first 512 characters")
    # The kept part still searches: the marker is found and marked.
    expect(page.locator("#search-results mark").first).to_have_text(
        token, timeout=INDEX_TIMEOUT
    )


# -- recent searches ---------------------------------------------------------------


def test_recent_searches_complete_what_you_type(
    fresh_session: tuple[Page, ApiClient],
) -> None:
    page, api = fresh_session
    token = marker("kingfisher")
    api.create_entry(today_utc() - timedelta(days=1), f"A {token} by the bridge")
    expect(search(page, token).first).to_be_visible(timeout=INDEX_TIMEOUT)

    page.reload()  # from the server's (encrypted) history, not this page's memory
    wait_for_app(page)
    field = page.locator("#search-input")
    field.click()
    field.press_sequentially(token[:6])

    expect(field).to_have_value(token)


def test_recent_searches_are_private(
    fresh_session: tuple[Page, ApiClient],
    new_context,
    make_user,
) -> None:
    page, api = fresh_session
    token = marker("nightjar")
    api.create_entry(today_utc() - timedelta(days=1), f"A {token} churring")
    expect(search(page, token).first).to_be_visible(timeout=INDEX_TIMEOUT)

    other = new_context().new_page()
    login(other, make_user("other"))
    field = other.locator("#search-input")
    field.click()
    field.press_sequentially(token[:6])
    other.wait_for_timeout(500)
    expect(field).to_have_value(token[:6])
    # Nor does searching for it find the first user's entry.
    field.fill(token)
    field.press("Enter")
    other.wait_for_timeout(1500)
    expect(other.locator("#search-results mark")).to_have_count(0)
