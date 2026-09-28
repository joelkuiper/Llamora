"""Traces (tags): adding, suggesting, removing, and the traces view."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from playwright.sync_api import Browser, Locator, Page, expect

from fake_llm import FakeLLM
from harness import ApiClient, LiveServer, login, marker, today_utc, wait_for_app


def today_entry(page: Page, api: ApiClient) -> tuple[Locator, str]:
    """Seed an entry for today, reload, and return (entry locator, text)."""
    text = f"Tagged thought {marker()}"
    entry_id = api.create_entry(today_utc(), text)
    page.goto("/d/today")
    wait_for_app(page)
    entry = page.locator(f"#entry-{entry_id}")
    expect(entry).to_contain_text(text)
    return entry, text


def open_tag_popover(page: Page, entry: Locator) -> Locator:
    entry.get_by_role("button", name="Add traces").click()
    popover = page.locator("#tag-popover-global")
    expect(popover).to_be_visible()
    return popover


def tag_on(entry: Locator, tag: str) -> Locator:
    return entry.locator(f'.entry-tag[data-tag-name="{tag}"]')


def test_add_a_trace_by_typing(app_page: Page, api: ApiClient) -> None:
    entry, _ = today_entry(app_page, api)
    tag = marker("trace")

    popover = open_tag_popover(app_page, entry)
    popover.locator('input[name="tag"]').fill(tag)
    popover.get_by_role("button", name="Add").click()

    expect(tag_on(entry, tag)).to_be_visible()
    app_page.reload()
    wait_for_app(app_page)
    expect(tag_on(entry, tag)).to_be_visible()


def test_add_a_trace_from_the_models_suggestions(
    app_page: Page, api: ApiClient, fake_llm: FakeLLM
) -> None:
    fake_llm.tags = [marker("walk"), marker("rain")]
    entry, _ = today_entry(app_page, api)
    chosen = fake_llm.tags[0]

    popover = open_tag_popover(app_page, entry)
    suggestion = popover.locator(f'.tag-suggestion[data-tag="{chosen}"]')
    expect(suggestion).to_be_visible()
    suggestion.click()

    expect(tag_on(entry, chosen)).to_be_visible()
    expect(suggestion).to_have_count(0)  # a used suggestion leaves the list
    expect(
        popover.locator(f'.tag-suggestion[data-tag="{fake_llm.tags[1]}"]')
    ).to_be_visible()


def test_remove_a_trace(app_page: Page, api: ApiClient) -> None:
    tag = marker("gone")
    text = f"Briefly tagged {marker()}"
    entry_id = api.create_entry(today_utc(), text)
    api.add_tag(entry_id, tag)
    app_page.goto("/d/today")
    wait_for_app(app_page)
    entry = app_page.locator(f"#entry-{entry_id}")
    expect(tag_on(entry, tag)).to_be_visible()

    tag_on(entry, tag).hover()  # the remove button is revealed on hover
    entry.get_by_role("button", name=f"Remove trace {tag}").click()

    expect(tag_on(entry, tag)).to_have_count(0)
    app_page.reload()
    wait_for_app(app_page)
    expect(tag_on(entry, tag)).to_have_count(0)


def test_trace_detail_leads_to_the_traces_view(app_page: Page, api: ApiClient) -> None:
    tag = marker("detail")
    entry_id = api.create_entry(today_utc(), f"Worth remembering {marker()}")
    api.add_tag(entry_id, tag)
    app_page.goto("/d/today")
    wait_for_app(app_page)

    tag_on(app_page.locator(f"#entry-{entry_id}"), tag).locator(".tag-label").click()
    detail = app_page.locator("#tag-detail-popover-global")
    expect(detail).to_be_visible()
    detail.get_by_role("link", name=f"Go to trace {tag}").click()

    expect(app_page).to_have_url(re.compile(rf"/t/{tag}(\?|$)"))
    expect(app_page.locator("#main-content")).to_have_attribute("data-view", "tags")
    expect(app_page.locator("#tags-view-detail")).to_have_attribute(
        "data-selected-tag", tag
    )

    app_page.go_back()
    expect(app_page).to_have_url(re.compile(r"/d/today$"))
    expect(app_page.locator("#main-content")).to_have_attribute("data-view", "diary")


def test_traces_view_links_to_the_entry_and_back(
    app_page: Page, api: ApiClient
) -> None:
    tag = marker("recall")
    day = today_utc() - timedelta(days=5)
    text = f"A tagged day in the past {marker()}"
    entry_id = api.create_entry(day, text)
    api.add_tag(entry_id, tag)

    app_page.goto(f"/t/{tag}")
    wait_for_app(app_page)
    item = app_page.locator(".tags-view__entry-item", has_text=text)
    expect(item).to_be_visible()

    item.get_by_role("link", name="Go to entry").click()
    expect(app_page).to_have_url(re.compile(rf"/d/{day.isoformat()}$"))
    expect(app_page.locator(f"#entry-{entry_id}")).to_be_in_viewport()

    app_page.go_back()
    expect(app_page).to_have_url(re.compile(rf"/t/{tag}(\?|$)"))
    expect(app_page.locator(".tags-view__entry-item", has_text=text)).to_be_visible()


# -- the heatmap's layout ---------------------------------------------------------

MONTH_BOXES = """() => [...document.querySelectorAll('.activity-heatmap__month')]
  .map(m => { const r = m.getBoundingClientRect();
              return {label: m.querySelector('.activity-heatmap__month-label').textContent.trim(),
                      top: Math.round(r.top), left: r.left, right: r.right,
                      label_left: m.querySelector('.activity-heatmap__month-label')
                        .getBoundingClientRect().left}; })"""


@pytest.mark.parametrize("width", [1600, 1440, 1280, 1000, 760, 390])
def test_the_heatmap_wraps_onto_the_same_columns(
    browser: Browser, live_server: LiveServer, make_user, width: int
) -> None:
    # Months that don't fit carry on in the next row from the left, on the
    # columns of the rows above: Aug under Oct, Sep under Nov; never spread.
    user = make_user("snake")
    api = ApiClient.logged_in(live_server.url, user)
    trace = marker("snake")
    api.add_tag(api.create_entry(today_utc(), "A day to trace"), trace)
    api.close()
    context = browser.new_context(
        viewport={"width": width, "height": 900}, base_url=live_server.url
    )
    page = context.new_page()
    login(page, user)
    page.goto(f"/t/{trace}")
    wait_for_app(page)
    expect(page.locator(".activity-heatmap__month").first).to_be_visible()
    page.wait_for_timeout(200)  # one layout frame

    months = page.evaluate(MONTH_BOXES)
    rows: list[list[dict]] = []
    for month in months:  # in calendar order
        if rows and abs(rows[-1][0]["top"] - month["top"]) <= 2:
            rows[-1].append(month)
        else:
            rows.append([month])
    context.close()

    if width >= 1440:  # there's room for the whole year on one row
        assert len(rows) == 1, [[m["label"] for m in row] for row in rows]
    columns = [m["left"] for m in rows[0]]
    for row in rows:
        lefts = [m["left"] for m in row]
        assert lefts == sorted(lefts), (width, row)  # left to right
        for left, column in zip(lefts, columns):
            assert abs(left - column) <= 2, (width, row)  # under the row above
    # Labels line up too: a year break starting a row isn't indented.
    firsts = [row[0]["label_left"] for row in rows]
    assert max(firsts) - min(firsts) <= 2, (width, firsts)
