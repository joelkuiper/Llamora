"""The entry composer: drafts, keyboard submission and the character counter."""

from __future__ import annotations

import re
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, expect

from harness import User, login, marker, wait_for_app

MAX_LENGTH = 12_000  # settings.LIMITS.max_message_length
COUNTER_THRESHOLD = 40  # data-char-threshold on the composer counter


def composer(page: Page):
    textarea = page.locator("#entry-text")
    expect(textarea).to_be_enabled()
    return textarea


def test_unsent_draft_survives_a_reload(app_page: Page) -> None:
    draft = f"Half a thought {marker()}"
    composer(app_page).fill(draft)

    app_page.reload()
    wait_for_app(app_page)

    expect(composer(app_page)).to_have_value(draft)
    composer(app_page).fill("")  # leave the shared user's draft clean


def test_draft_is_cleared_once_sent(app_page: Page) -> None:
    text = f"Sent and done {marker()}"
    composer(app_page).fill(text)
    composer(app_page).press("Enter")
    expect(app_page.locator("#entries .entry.user", has_text=text)).to_be_visible()

    app_page.reload()
    wait_for_app(app_page)

    expect(composer(app_page)).to_have_value("")


def test_draft_does_not_leak_to_the_next_user_in_the_same_tab(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> None:
    page = new_context().new_page()
    login(page, make_user("writer"))
    secret = f"Something private {marker()}"
    composer(page).fill(secret)

    page.get_by_role("button", name="Logout").click()
    expect(page).to_have_url(re.compile(r"/login"))
    login(page, make_user("next"))

    expect(composer(page)).to_have_value("")


def test_enter_sends_and_shift_enter_adds_a_line(app_page: Page) -> None:
    first, second = f"First line {marker()}", f"second line {marker()}"
    entries = app_page.locator("#entries .entry.user")
    before = entries.count()

    textarea = composer(app_page)
    textarea.press_sequentially(first)
    textarea.press("Shift+Enter")
    textarea.press_sequentially(second)
    expect(textarea).to_have_value(f"{first}\n{second}")
    expect(entries).to_have_count(before)  # Shift+Enter did not send

    textarea.press("Enter")
    sent = app_page.locator("#entries .entry.user", has_text=first)
    expect(sent).to_contain_text(second)
    expect(entries).to_have_count(before + 1)


def test_empty_composer_cannot_be_sent(app_page: Page) -> None:
    entries = app_page.locator("#entries .entry.user")
    before = entries.count()
    textarea = composer(app_page)

    textarea.fill("   ")
    expect(app_page.locator("#send-btn")).to_be_disabled()
    textarea.press("Enter")

    expect(entries).to_have_count(before)
    textarea.fill("")


def test_character_counter_appears_near_the_limit(app_page: Page) -> None:
    textarea = composer(app_page)
    counter = app_page.locator('[data-char-counter-for="entry-text"]')

    textarea.fill("x" * 100)
    expect(counter).not_to_have_class(re.compile(r"\bis-visible\b"))

    textarea.fill("x" * (MAX_LENGTH - COUNTER_THRESHOLD))
    expect(counter).to_have_text(f"{COUNTER_THRESHOLD} left")
    expect(counter).to_have_class(re.compile(r"\bis-visible\b"))

    textarea.fill("x" * MAX_LENGTH)
    expect(counter).to_have_text("0 left")
    expect(counter).to_have_class(re.compile(r"\bis-limit\b"))
    textarea.press("y")  # maxlength stops further typing
    expect(textarea).to_have_value("x" * MAX_LENGTH)

    textarea.fill("")
    expect(counter).not_to_have_class(re.compile(r"\bis-visible\b"))
