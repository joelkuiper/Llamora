"""Streaming replies: leaving mid-stream, reattaching, stopping, capacity.

Leaving a page (reload, navigation, closing the tab) only ends that page's
subscription. The generation finishes in the background and is saved; a
returning page reattaches to it. Only the explicit Stop button truncates.

Every Respond click is a new generation with its own stream id: responding
again to an entry never replays an earlier reply.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, expect

from fake_llm import FakeLLM
from harness import (
    ApiClient,
    User,
    marker,
    submit_login,
    today_utc,
    wait_for_app,
    write_entry,
)

SLOW_CHUNK = 0.25  # seconds per word: slow enough to leave mid-stream


def slow_reply(fake_llm: FakeLLM, label: str) -> str:
    fake_llm.chunk_delay = SLOW_CHUNK
    fake_llm.reply = f"One two three four five six seven eight {marker(label)}"
    return fake_llm.reply


def test_reply_survives_reloading_mid_stream(app_page: Page, fake_llm: FakeLLM) -> None:
    reply = slow_reply(fake_llm, "reload")
    entry = write_entry(app_page, f"Reload while it answers {marker()}")
    entry_id = entry.get_attribute("data-entry-id")

    entry.get_by_role("button", name="Respond").click()
    expect(app_page.locator(f"#entry-responses-{entry_id}")).to_contain_text("One two")
    app_page.reload()

    # The reloaded page reattaches to the running generation and catches up.
    responses = app_page.locator(f"#entry-responses-{entry_id}")
    expect(responses).to_contain_text(reply, timeout=10_000)
    wait_for_app(app_page)
    assert len(fake_llm.chat_requests(structured=False)) == 1, "reply was regenerated"

    app_page.reload()
    wait_for_app(app_page)
    expect(app_page.locator(f"#entry-responses-{entry_id}")).to_contain_text(reply)


def test_reloaded_page_offers_stop_while_reply_is_generating(
    app_page: Page, fake_llm: FakeLLM
) -> None:
    slow_reply(fake_llm, "stoppable")
    entry = write_entry(app_page, f"Still answering {marker()}")
    entry_id = entry.get_attribute("data-entry-id")
    entry.get_by_role("button", name="Respond").click()
    expect(app_page.locator(f"#entry-responses-{entry_id}")).to_contain_text("One two")

    app_page.reload()

    actions = app_page.locator(f"#entry-actions-{entry_id}")
    expect(actions.get_by_role("button", name="Stop response")).to_be_visible()
    wait_for_app(app_page)
    expect(actions.get_by_role("button", name="Respond")).to_be_visible()


def test_responding_again_generates_a_new_reply(
    app_page: Page, fake_llm: FakeLLM
) -> None:
    entry = write_entry(app_page, f"Ask me twice {marker()}")
    entry_id = entry.get_attribute("data-entry-id")
    first = fake_llm.reply = f"First thoughts {marker('first')}"
    entry.get_by_role("button", name="Respond").click()
    expect(app_page.locator(f"#entry-responses-{entry_id}")).to_contain_text(first)
    wait_for_app(app_page)

    app_page.reload()
    wait_for_app(app_page)
    second = fake_llm.reply = f"Second thoughts {marker('second')}"
    app_page.locator(f"#entry-{entry_id}").get_by_role("button", name="Respond").click()

    responses = app_page.locator(f"#entry-responses-{entry_id} .markdown-body")
    expect(responses).to_have_count(2)
    expect(responses.nth(0)).to_contain_text(first)
    expect(responses.nth(1)).to_contain_text(second)
    wait_for_app(app_page)
    assert len(fake_llm.chat_requests(structured=False)) == 2


def test_day_opening_survives_leaving_mid_stream(
    new_context: Callable[..., BrowserContext],
    make_user: Callable[..., User],
    fake_llm: FakeLLM,
) -> None:
    opening = slow_reply(fake_llm, "opening")
    page = new_context().new_page()
    submit_login(page, make_user("leaver"))
    expect(page.locator("#entries .entry--opening")).to_contain_text("One two")

    page.reload()

    expect(page.locator("#entries .entry--opening")).to_contain_text(
        opening, timeout=10_000
    )
    wait_for_app(page)
    assert len(fake_llm.chat_requests(structured=False)) == 1, "opening was regenerated"


def test_stop_keeps_the_partial_reply(app_page: Page, fake_llm: FakeLLM) -> None:
    reply = slow_reply(fake_llm, "stopped")
    entry = write_entry(app_page, f"Stop it halfway {marker()}")
    entry_id = entry.get_attribute("data-entry-id")
    responses = app_page.locator(f"#entry-responses-{entry_id}")

    entry.get_by_role("button", name="Respond").click()
    expect(responses).to_contain_text("One two")
    entry.get_by_role("button", name="Stop response").click()
    wait_for_app(app_page)

    app_page.reload()
    wait_for_app(app_page)
    saved = app_page.locator(f"#entry-responses-{entry_id}")
    expect(saved).to_contain_text("One two")
    expect(saved).not_to_contain_text(reply.split()[-1])


def test_abandoned_replies_do_not_exhaust_llm_slots(
    app_page: Page, api: ApiClient, fake_llm: FakeLLM
) -> None:
    # The fake LLM advertises 4 parallel slots; abandon more replies than that.
    reply = slow_reply(fake_llm, "busy")
    entry_ids = [
        api.create_entry(today_utc(), f"Busy day {marker()}") for _ in range(5)
    ]
    for entry_id in entry_ids:
        app_page.goto("/d/today")
        entry = app_page.locator(f"#entry-{entry_id}")
        entry.get_by_role("button", name="Respond").click()
        expect(app_page.locator(f"#entry-responses-{entry_id}")).to_contain_text("One")

    # Every abandoned reply still completes and is saved...
    app_page.goto("/d/today")
    wait_for_app(app_page)
    for entry_id in entry_ids:
        expect(app_page.locator(f"#entry-responses-{entry_id}")).to_contain_text(reply)

    # ...and the slots are free again for a new reply.
    fake_llm.chunk_delay = 0.01
    fresh = write_entry(app_page, f"After the rush {marker()}")
    fresh_id = fresh.get_attribute("data-entry-id")
    fresh.get_by_role("button", name="Respond").click()
    expect(app_page.locator(f"#entry-responses-{fresh_id}")).to_contain_text(
        reply, timeout=10_000
    )


# --- failures ---------------------------------------------------------------


def test_upstream_failure_shows_an_error_and_respond_recovers(
    app_page: Page, fake_llm: FakeLLM
) -> None:
    fake_llm.fail_status = 500
    entry = write_entry(app_page, f"The model is down {marker()}")
    entry_id = entry.get_attribute("data-entry-id")
    responses = app_page.locator(f"#entry-responses-{entry_id}")

    entry.get_by_role("button", name="Respond").click()

    expect(responses.locator(".entry--error")).to_be_visible(timeout=15_000)
    wait_for_app(app_page)
    expect(entry.get_by_role("button", name="Respond")).to_be_enabled()
    expect(app_page.locator("#entry-text")).to_be_enabled()

    # Once the model is back, responding again works.
    fake_llm.fail_status = None
    fake_llm.reply = f"Back online {marker('ok')}"
    entry.get_by_role("button", name="Respond").click()
    expect(responses).to_contain_text(fake_llm.reply)


def test_failure_mid_reply_keeps_the_partial_text_and_flags_it(
    app_page: Page, fake_llm: FakeLLM
) -> None:
    fake_llm.reply = "Alpha bravo charlie delta echo foxtrot"
    fake_llm.fail_after = 3
    entry = write_entry(app_page, f"Cut me off {marker()}")
    entry_id = entry.get_attribute("data-entry-id")

    entry.get_by_role("button", name="Respond").click()

    responses = app_page.locator(f"#entry-responses-{entry_id}")
    expect(responses.locator(".entry--error")).to_be_visible(timeout=15_000)
    expect(responses).to_contain_text("Alpha bravo charlie")
    expect(responses).not_to_contain_text("delta")
    wait_for_app(app_page)

    app_page.reload()
    wait_for_app(app_page)
    saved = app_page.locator(f"#entry-responses-{entry_id}")
    expect(saved).to_contain_text("Alpha bravo charlie")


def test_busy_assistant_says_so(
    app_page: Page, api: ApiClient, fake_llm: FakeLLM
) -> None:
    # 4 slots + a queue of 4: eight slow replies saturate the stream manager.
    fake_llm.chunk_delay = 1.0
    fake_llm.reply = "one two three four five six seven eight nine ten"
    today = today_utc()
    busy = [api.create_entry(today, f"Busy {marker()}") for _ in range(8)]
    try:
        for entry_id in busy:
            assert api.start_reply(entry_id, today) == 200
        entry = write_entry(app_page, f"One more please {marker()}")
        entry_id = entry.get_attribute("data-entry-id")

        entry.get_by_role("button", name="Respond").click()

        responses = app_page.locator(f"#entry-responses-{entry_id}")
        expect(responses).to_contain_text("The assistant is busy", timeout=10_000)
    finally:
        for entry_id in busy:
            api.stop_replies(entry_id)
