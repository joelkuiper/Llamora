"""Traces (tags): adding, suggesting, removing, and the traces view."""

from __future__ import annotations

import re
from datetime import timedelta

from playwright.sync_api import Locator, Page, expect

from fake_llm import FakeLLM
from harness import ApiClient, marker, today_utc, wait_for_app


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
