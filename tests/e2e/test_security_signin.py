"""A guarded server: registration closed, login lockout, cookies forced Secure.

One server for the module, configured the way a public instance might be:
registration needs the one-time token from the startup log, three failed
logins lock a username (for three seconds here), and cookies are marked Secure.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from fake_llm import FakeLLM
from harness import LiveServer, new_credentials, start_server

_CSRF_INPUT_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
_TOKEN_RE = re.compile(r"/register\?token=([\w-]+)")

MAX_ATTEMPTS = 3
LOCKOUT_SECONDS = 3


@dataclass(slots=True)
class Guarded:
    server: LiveServer
    token: str
    username: str
    password: str
    registration: httpx.Response


def csrf(client: httpx.Client, path: str) -> str:
    page = client.get(path)
    assert page.status_code == 200, (path, page.status_code)
    match = _CSRF_INPUT_RE.search(page.text)
    assert match
    return match.group(1)


def attempt(guarded: Guarded, password: str, *, username: str | None = None, **kw):
    with httpx.Client(base_url=guarded.server.url, timeout=30.0) as client:
        return client.post(
            "/login",
            data={
                "username": username or guarded.username,
                "password": password,
                "csrf_token": csrf(client, "/login"),
            },
            **kw,
        )


@pytest.fixture(scope="module")
def guarded(
    tmp_path_factory: pytest.TempPathFactory, fake_llm: FakeLLM
) -> Iterator[Guarded]:
    server = start_server(
        tmp_path_factory.mktemp("guarded"),
        llm_url=fake_llm.url,
        extra_env={
            "LLAMORA_FEATURES__DISABLE_REGISTRATION": "true",
            "LLAMORA_AUTH__MAX_LOGIN_ATTEMPTS": str(MAX_ATTEMPTS),
            "LLAMORA_AUTH__LOGIN_LOCKOUT_TTL": str(LOCKOUT_SECONDS),
            "LLAMORA_COOKIES__FORCE_SECURE": "true",
        },
    )
    try:
        found = _TOKEN_RE.search(Path(server.log_path).read_text(encoding="utf-8"))
        assert found, "no one-time registration URL in the startup log"
        token = found.group(1)
        user = new_credentials("guard")
        with httpx.Client(base_url=server.url, timeout=30.0) as client:
            registration = client.post(
                f"/register?token={token}",
                data={
                    "username": user.username,
                    "password": user.password,
                    "confirm_password": user.password,
                    "csrf_token": csrf(client, f"/register?token={token}"),
                },
            )
        assert registration.status_code == 200, registration.text[:300]
        assert 'id="recovery"' in registration.text
        yield Guarded(server, token, user.username, user.password, registration)
    finally:
        server.stop()


# -- registration ---------------------------------------------------------------


def test_registration_is_closed_without_the_token(guarded: Guarded) -> None:
    base = guarded.server.url
    assert httpx.get(f"{base}/register").status_code == 404
    assert httpx.get(f"{base}/register?token=guess").status_code == 404
    assert httpx.post(f"{base}/register", data={"username": "x"}).status_code in (
        400,
        404,
    )


def test_the_token_works_only_once(guarded: Guarded) -> None:
    # The fixture used it for the first account.
    resp = httpx.get(f"{guarded.server.url}/register?token={guarded.token}")
    assert resp.status_code == 404


def test_cookies_are_secure_when_forced(guarded: Guarded) -> None:
    cookie = next(
        c
        for c in guarded.registration.headers.get_list("set-cookie")
        if c.startswith("llamora=")
    )
    flags = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"secure", "httponly", "samesite=lax"} <= flags


# -- lockout --------------------------------------------------------------------


def test_a_successful_login_resets_the_count(guarded: Guarded) -> None:
    for _ in range(2):
        for _ in range(MAX_ATTEMPTS - 1):
            assert attempt(guarded, "wrong password").status_code == 200
        assert attempt(guarded, guarded.password).status_code == 302


def alert_text(resp: httpx.Response) -> str:
    found = re.search(r'<div class="alert__message">(.*?)</div>', resp.text, re.S)
    assert found, "no message on the login page"
    return " ".join(found.group(1).split())


def test_the_last_tries_are_counted_down(guarded: Guarded) -> None:
    first = attempt(guarded, "wrong password")
    second = attempt(guarded, "wrong password")

    assert first.status_code == second.status_code == 200
    assert "2 more tries before sign-in pauses" in alert_text(first)
    assert "1 more try before sign-in pauses" in alert_text(second)
    assert attempt(guarded, guarded.password).status_code == 302  # resets


def test_failed_logins_pause_sign_in_and_say_for_how_long(guarded: Guarded) -> None:
    for _ in range(MAX_ATTEMPTS - 1):
        assert attempt(guarded, "wrong password").status_code == 200
    last = attempt(guarded, "wrong password")

    # The try that uses up the last attempt already explains the pause.
    assert last.status_code == 429
    assert "paused" in alert_text(last)

    # While paused, even the right password is refused, on the login page
    # itself: the username is kept, and it says when to try again.
    locked = attempt(guarded, guarded.password)
    assert locked.status_code == 429
    message = alert_text(locked)
    assert "Sign-in for this account is paused" in message
    assert "Try again in under a minute" in message
    assert "reset your password" in message
    assert f'value="{guarded.username}"' in locked.text
    assert 'name="password"' in locked.text
    assert 0 < int(locked.headers["retry-after"]) <= LOCKOUT_SECONDS

    # A forged client address (no trusted proxy is configured) doesn't help.
    spoofed = attempt(
        guarded, guarded.password, headers={"X-Forwarded-For": "203.0.113.9"}
    )
    assert spoofed.status_code == 429

    time.sleep(LOCKOUT_SECONDS + 1)
    assert attempt(guarded, guarded.password).status_code == 302


def test_guessing_other_usernames_does_not_lock_this_one(guarded: Guarded) -> None:
    for _ in range(MAX_ATTEMPTS + 1):
        attempt(guarded, "wrong", username="nobody-" + guarded.username)

    assert attempt(guarded, guarded.password).status_code == 302
