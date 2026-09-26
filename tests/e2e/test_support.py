"""Help is always one click away, and the model is told how to respond to a
crisis (with only the configured resources)."""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import marker, wait_for_app, write_entry
from timekit import reply_requests


def test_need_someone_to_talk_to_is_always_in_the_header(app_page: Page) -> None:
    app_page.get_by_role("link", name="Need someone to talk to?").click()

    modal = app_page.locator("[data-profile-modal]")
    expect(modal).to_be_visible()
    expect(modal.get_by_role("tab", name="Support")).to_have_attribute(
        "aria-selected", "true"
    )
    expect(
        modal.get_by_role("heading", name="Need someone to talk to?")
    ).to_be_visible()
    link = modal.get_by_role("link", name="findahelpline.com")
    expect(link).to_have_attribute("href", "https://findahelpline.com")
    expect(link).to_have_attribute("target", "_blank")
    expect(modal).to_contain_text("contact your local emergency number")


def test_the_support_tab_is_also_in_the_profile(app_page: Page) -> None:
    app_page.get_by_role("link", name="Profile").click()
    modal = app_page.locator("[data-profile-modal]")
    modal.get_by_role("tab", name="Support").click()

    expect(modal.get_by_role("link", name="findahelpline.com")).to_be_visible()


def test_the_support_link_works_as_a_plain_url(app_page: Page) -> None:
    app_page.goto("/profile?tab=support")

    wait_for_app(app_page)
    expect(app_page).to_have_url(re.compile(r"/d/"))
    modal = app_page.locator("[data-profile-modal]")
    expect(modal.get_by_role("link", name="findahelpline.com")).to_be_visible()


def test_replies_carry_the_care_guidance_and_resources(
    app_page: Page, fake_llm: FakeLLM
) -> None:
    entry = write_entry(app_page, f"A heavy day {marker()}")
    entry.get_by_role("button", name="Respond").click()
    expect(
        app_page.locator(f"#entry-responses-{entry.get_attribute('data-entry-id')}")
    ).to_contain_text(fake_llm.reply)
    wait_for_app(app_page)

    system = reply_requests(fake_llm)[-1]["messages"][0]["content"]
    assert "ask whether they are safe right now" in system
    assert "- Find A Helpline (https://findahelpline.com)" in system
