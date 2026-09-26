"""Diary: writing, replying to, editing and deleting entries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from playwright.sync_api import BrowserContext, Page, expect

from fake_llm import FakeLLM
from harness import (
    ApiClient,
    User,
    login,
    marker,
    today_utc,
    wait_for_app,
    wait_for_streams_idle,
    write_entry,
)


def test_first_visit_streams_a_day_opening(
    new_context: Callable[..., BrowserContext],
    make_user: Callable[..., User],
    fake_llm: FakeLLM,
) -> None:
    fake_llm.reply = f"Good morning, {marker('opening')}. What is on your mind?"
    page = new_context().new_page()
    login(page, make_user("opening"))

    opening = page.locator("#entries .entry--opening")
    expect(opening).to_contain_text(fake_llm.reply)
    expect(page.locator("#entry-text")).to_be_enabled()


def test_written_entry_appears_and_persists(app_page: Page) -> None:
    text = f"Walked along the canal this morning {marker()}"

    write_entry(app_page, text)

    expect(app_page.locator("#entry-text")).to_have_value("")
    app_page.reload()
    wait_for_app(app_page)
    expect(app_page.locator("#entries .entry.user", has_text=text)).to_be_visible()


def test_respond_streams_a_reply_from_the_model(
    app_page: Page, fake_llm: FakeLLM
) -> None:
    text = f"Could not sleep, kept thinking about work {marker()}"
    fake_llm.reply = f"That sounds exhausting. {marker('reply')}"
    entry = write_entry(app_page, text)
    entry_id = entry.get_attribute("data-entry-id")

    entry.get_by_role("button", name="Respond").click()

    responses = app_page.locator(f"#entry-responses-{entry_id}")
    expect(responses).to_contain_text(fake_llm.reply)
    chats = fake_llm.chat_requests(structured=False)
    assert any(text in str(req.get("messages")) for req in chats), (
        "entry not sent to model"
    )

    wait_for_streams_idle(app_page)  # the text shows before the stream finalises
    app_page.reload()
    wait_for_app(app_page)
    expect(app_page.locator(f"#entry-responses-{entry_id}")).to_contain_text(
        fake_llm.reply
    )


def test_edit_entry_replaces_its_text(app_page: Page) -> None:
    original = f"First draft of a thought {marker()}"
    edited = f"Second, better draft {marker()}"
    entry = write_entry(app_page, original)

    entry.get_by_role("button", name="Edit entry").click()
    editor = entry.locator(".entry-edit-area")
    expect(editor).to_have_value(original)
    editor.fill(edited)
    entry.get_by_role("button", name="Save entry").click()

    expect(entry.locator(".markdown-body")).to_have_text(edited)
    app_page.reload()
    wait_for_app(app_page)
    expect(app_page.locator("#entries .entry.user", has_text=edited)).to_be_visible()
    expect(app_page.locator("#entries .entry.user", has_text=original)).to_have_count(0)


def test_cancel_edit_keeps_original_text(app_page: Page) -> None:
    original = f"Something I will not change {marker()}"
    entry = write_entry(app_page, original)

    entry.get_by_role("button", name="Edit entry").click()
    entry.locator(".entry-edit-area").fill("discarded words")
    entry.get_by_role("button", name="Cancel editing").first.click()

    expect(entry.locator(".markdown-body")).to_have_text(original)


def test_delete_entry_asks_for_confirmation(app_page: Page) -> None:
    text = f"A note I will regret {marker()}"
    entry = write_entry(app_page, text)
    modal = app_page.locator("#confirm-modal")

    entry.get_by_role("button", name="Delete entry").click()
    expect(modal).to_be_visible()
    modal.get_by_role("button", name="Keep").click()
    expect(modal).to_be_hidden()
    expect(entry).to_be_visible()

    entry.get_by_role("button", name="Delete entry").click()
    modal.get_by_role("button", name="Delete").click()
    expect(app_page.locator("#entries .entry.user", has_text=text)).to_have_count(0)

    app_page.reload()
    wait_for_app(app_page)
    expect(app_page.locator("#entries .entry.user", has_text=text)).to_have_count(0)


def test_past_days_are_read_only(app_page: Page, api: ApiClient) -> None:
    day = today_utc() - timedelta(days=12)
    text = f"An old memory {marker()}"
    api.create_entry(day, text)

    app_page.goto(f"/d/{day.isoformat()}")
    wait_for_app(app_page)

    entry = app_page.locator("#entries .entry.user", has_text=text)
    expect(entry).to_be_visible()
    expect(app_page.locator("#entry-form")).to_have_count(0)
    expect(app_page.get_by_role("link", name="Return to the present")).to_be_visible()
    expect(entry.get_by_role("button", name="Edit entry")).to_be_disabled()
    expect(entry.get_by_role("button", name="Respond")).to_be_disabled()
