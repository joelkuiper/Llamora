"""Account flows that re-wrap the encryption key: a mistake here loses data.

Entries are encrypted with a data key (DEK) wrapped by the password and,
separately, by the recovery code. Every flow below must leave old entries
readable with the new credential, and the old credential useless.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Locator, Page, expect

from harness import User, login, marker, wait_for_app, write_entry

INVALID = "That username and password don't match."


def new_password() -> str:
    # Satisfies the reset form's pattern (letters and digits) and zxcvbn.
    return f"lantern-{secrets.token_hex(6)}-harbor9"


def signed_in(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> tuple[Page, User, str]:
    """A fresh user with one entry written through the UI."""
    user = make_user("acct")
    page = new_context().new_page()
    login(page, user)
    text = f"Something worth keeping {marker()}"
    write_entry(page, text)
    return page, user, text


def open_profile(page: Page, tab: str) -> Locator:
    page.get_by_role("link", name="Profile").click()
    modal = page.locator("[data-profile-modal]")
    expect(modal).to_be_visible()
    modal.get_by_role("tab", name=tab).click()
    expect(modal.locator(f'[data-profile-tab="{tab.lower()}"]')).to_have_attribute(
        "aria-selected", "true"
    )
    return modal


def log_out(page: Page) -> None:
    page.get_by_role("button", name="Logout").click()
    expect(page).to_have_url(re.compile(r"/login"))


def attempt_login(page: Page, username: str, password: str) -> None:
    page.goto("/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.get_by_role("button", name="Login").click()


def expect_entry_readable(page: Page, text: str) -> None:
    expect(page).to_have_url(re.compile(r"/d/"))
    wait_for_app(page)
    expect(page.locator("#entries .entry.user", has_text=text)).to_be_visible()


def reset_with_recovery_code(
    page: Page, username: str, code: str, password: str
) -> None:
    page.goto("/login")
    page.get_by_role("link", name="Forgot password?").click()
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="recovery_code"]').fill(code)
    page.locator('input[name="new_password"]').fill(password)
    page.locator('input[name="confirm_password"]').fill(password)
    page.get_by_role("button", name="Reset").click()


def test_change_password_keeps_entries_readable(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> None:
    page, user, text = signed_in(new_context, make_user)
    password = new_password()

    modal = open_profile(page, "Security")
    modal.locator('input[name="current_password"]').fill(user.password)
    modal.locator('input[name="new_password"]').fill(password)
    modal.locator('input[name="confirm_password"]').fill(password)
    modal.get_by_role("button", name="Update Password").click()
    expect(modal.get_by_text("Password updated")).to_be_visible()

    page.goto("/d/today")
    log_out(page)
    attempt_login(page, user.username, user.password)
    expect(page.get_by_text(INVALID)).to_be_visible()
    attempt_login(page, user.username, password)
    expect_entry_readable(page, text)


def test_change_password_rejects_wrong_current_password(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> None:
    page, user, text = signed_in(new_context, make_user)
    password = new_password()

    modal = open_profile(page, "Security")
    modal.locator('input[name="current_password"]').fill("definitely-not-it-1")
    modal.locator('input[name="new_password"]').fill(password)
    modal.locator('input[name="confirm_password"]').fill(password)
    modal.get_by_role("button", name="Update Password").click()
    expect(modal.get_by_text("Password updated")).to_have_count(0)
    expect(modal.locator(".alert, [role='alert']").first).to_be_visible()

    page.goto("/d/today")
    log_out(page)
    attempt_login(page, user.username, user.password)
    expect_entry_readable(page, text)


def test_recovery_code_resets_the_password(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> None:
    page, user, text = signed_in(new_context, make_user)
    password = new_password()
    log_out(page)

    reset_with_recovery_code(page, user.username, user.recovery_code, password)

    expect(page).to_have_url(re.compile(r"/login"))
    attempt_login(page, user.username, user.password)
    expect(page.get_by_text(INVALID)).to_be_visible()
    attempt_login(page, user.username, password)
    expect_entry_readable(page, text)


def test_regenerated_recovery_code_replaces_the_old_one(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> None:
    page, user, text = signed_in(new_context, make_user)

    modal = open_profile(page, "Security")
    modal.get_by_role("button", name="Generate New Recovery Code").click()
    fresh_code = modal.locator(".profile-recovery__code")
    expect(fresh_code).to_be_visible()
    code = fresh_code.inner_text().strip()
    assert code and code != user.recovery_code

    page.goto("/d/today")
    log_out(page)
    reset_with_recovery_code(page, user.username, user.recovery_code, new_password())
    expect(page.get_by_text("Invalid recovery code")).to_be_visible()

    password = new_password()
    reset_with_recovery_code(page, user.username, code, password)
    expect(page).to_have_url(re.compile(r"/login"))
    attempt_login(page, user.username, password)
    expect_entry_readable(page, text)


def test_data_export_contains_decrypted_entries(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> None:
    page, user, text = signed_in(new_context, make_user)

    modal = open_profile(page, "Data")
    with page.expect_download() as download_info:
        modal.get_by_role("button", name="Download My Data").click()
    download = download_info.value

    assert download.suggested_filename == "user_data.json"
    data = json.loads(open(download.path(), encoding="utf-8").read())
    assert data["user"]["username"] == user.username
    assert any(text in json.dumps(message) for message in data["messages"])


def test_delete_account_signs_out_and_removes_the_user(
    new_context: Callable[..., BrowserContext], make_user: Callable[..., User]
) -> None:
    page, user, _ = signed_in(new_context, make_user)

    modal = open_profile(page, "Account")
    modal.get_by_role("button", name="Delete Profile").click()
    confirm = page.locator("#confirm-modal")
    expect(confirm).to_be_visible()
    confirm.get_by_role("button", name="Delete profile").click()

    expect(page).to_have_url(re.compile(r"/login"))
    attempt_login(page, user.username, user.password)
    expect(page.get_by_text(INVALID)).to_be_visible()
