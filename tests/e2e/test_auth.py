"""Smoke tests: registration, login and logout as a real user would do them."""

from __future__ import annotations

import re
from collections.abc import Callable

from playwright.sync_api import Page, expect

from harness import User, login, new_credentials, wait_for_app

TODAY_URL = re.compile(r"/d/today$")


def test_register_shows_recovery_code_then_opens_today(page: Page) -> None:
    user = new_credentials("register")

    page.goto("/register")
    page.locator('input[name="username"]').fill(user.username)
    page.locator('input[name="password"]').fill(user.password)
    page.locator('input[name="confirm_password"]').fill(user.password)
    page.get_by_role("button", name="Create account").click()

    expect(page.locator("#recovery")).not_to_be_empty()
    continue_btn = page.get_by_role("button", name="Continue")
    expect(continue_btn).to_be_disabled()

    page.locator("#acknowledge").check()
    expect(continue_btn).to_be_enabled()
    continue_btn.click()

    expect(page).to_have_url(TODAY_URL)
    wait_for_app(page)
    expect(page.locator("#main-content")).to_have_attribute("data-view", "diary")
    expect(page.locator("#entry-text")).to_be_visible()


def test_protected_pages_redirect_to_login(page: Page) -> None:
    page.goto("/d/today")

    expect(page).to_have_url(re.compile(r"/login\?return=%2Fd%2Ftoday$"))


def test_logout_then_login_returns_to_requested_page(
    page: Page, make_user: Callable[..., User]
) -> None:
    user = make_user("relogin")
    login(page, user)
    expect(page).to_have_url(TODAY_URL)

    page.get_by_role("button", name="Logout").click()
    expect(page).to_have_url(re.compile(r"/login"))

    page.goto("/d/today")
    expect(page).to_have_url(re.compile(r"/login\?return="))
    page.locator('input[name="username"]').fill(user.username)
    page.locator('input[name="password"]').fill(user.password)
    page.get_by_role("button", name="Login").click()

    expect(page).to_have_url(TODAY_URL)


def test_wrong_password_is_rejected(page: Page, make_user: Callable[..., User]) -> None:
    user = make_user("badpass")

    page.goto("/login")
    page.locator('input[name="username"]').fill(user.username)
    page.locator('input[name="password"]').fill("not-the-password")
    page.get_by_role("button", name="Login").click()

    expect(page.get_by_text("Invalid credentials")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/login"))
