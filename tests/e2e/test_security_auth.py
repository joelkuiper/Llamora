"""Signing in safely: where login may send you, and the headers every page carries.

Plain HTTP (no browser): these are properties of responses, checked the way a
browser would interpret them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx
import pytest

from harness import ApiClient, LiveServer, User

_CSRF_INPUT_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')

# Each of these would take the browser to another site if echoed into Location.
OFFSITE_RETURNS = [
    "//evil.example/",
    "/\\evil.example/",  # browsers read "\" as "/": //evil.example
    "/\\/evil.example/",
    "/\t/evil.example/",  # tabs and newlines are stripped from URLs
    "/\n/evil.example/",
    "https://evil.example/",
    "javascript:alert(1)",
    "\\\\evil.example",
]
SAFE_RETURNS = ["/d/2026-01-02", "/t?day=2026-01-02", "/"]


def browser_target(base: str, location: str) -> str:
    """Where a browser goes for ``Location: location`` (WHATWG URL parsing):
    tabs and newlines are dropped and backslashes count as slashes."""
    cleaned = re.sub(r"[\t\n\r]", "", location).replace("\\", "/")
    if cleaned.startswith("//"):  # any run of slashes starts a host: ///x is //x
        cleaned = "//" + cleaned.lstrip("/")
    return urljoin(base, cleaned)


def stays_on_site(base: str, location: str) -> bool:
    target = urlsplit(browser_target(base, location))
    return target.scheme in ("http", "https") and target.netloc == urlsplit(base).netloc


def post_login(base: str, user: User, return_to: str) -> httpx.Response:
    with httpx.Client(base_url=base, timeout=30.0) as client:
        page = client.get("/login")
        token = _CSRF_INPUT_RE.search(page.text)
        assert token
        return client.post(
            "/login",
            data={
                "username": user.username,
                "password": user.password,
                "csrf_token": token.group(1),
                "return": return_to,
            },
        )


# -- where login sends you ----------------------------------------------------


@pytest.mark.parametrize("return_to", OFFSITE_RETURNS)
def test_login_never_redirects_off_site(
    live_server: LiveServer, make_user: Callable[..., User], return_to: str
) -> None:
    resp = post_login(live_server.url, make_user("redir"), return_to)

    assert resp.status_code in (302, 303), resp.status_code
    location = resp.headers["location"]
    assert stays_on_site(live_server.url, location), location


@pytest.mark.parametrize("return_to", OFFSITE_RETURNS)
def test_a_signed_in_visit_to_login_never_redirects_off_site(
    live_server: LiveServer, make_user: Callable[..., User], return_to: str
) -> None:
    api = ApiClient.logged_in(live_server.url, make_user("redir"))
    try:
        resp = api.http.get("/login", params={"return": return_to})
    finally:
        api.close()

    assert resp.status_code in (302, 303), resp.status_code
    assert stays_on_site(live_server.url, resp.headers["location"])


@pytest.mark.parametrize("return_to", SAFE_RETURNS)
def test_login_returns_to_the_page_you_came_from(
    live_server: LiveServer, make_user: Callable[..., User], return_to: str
) -> None:
    resp = post_login(live_server.url, make_user("redir"), return_to)

    assert resp.headers["location"] == return_to


# -- headers on every page ----------------------------------------------------


def csp_directives(header: str) -> dict[str, str]:
    directives = {}
    for part in header.split(";"):
        name, _, value = part.strip().partition(" ")
        if name:
            directives[name.lower()] = value.strip()
    return directives


@pytest.mark.parametrize(
    ("path", "signed_in"),
    [("/login", False), ("/register", False), ("/d/today", True), ("/t", True)],
)
def test_pages_cannot_be_framed_or_sniffed(
    api: ApiClient, live_server: LiveServer, path: str, signed_in: bool
) -> None:
    if signed_in:
        resp = api.http.get(path)
    else:
        resp = httpx.get(live_server.url + path, timeout=30.0)
    assert resp.status_code == 200, (path, resp.status_code)
    headers = resp.headers

    csp = csp_directives(headers.get("content-security-policy", ""))
    assert csp.get("frame-ancestors") == "'none'", headers.get(
        "content-security-policy"
    )
    assert csp.get("object-src") == "'none'"
    assert csp.get("base-uri") == "'self'"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("x-content-type-options") == "nosniff"
    # Same-origin only: diary URLs (dates, traces) never leak to linked sites,
    # while the app's own requests keep the Referer that CSRF checks rely on.
    assert headers.get("referrer-policy") == "same-origin"


def test_images_keep_their_own_stricter_policy(api: ApiClient) -> None:
    from imaging import encode, halves

    image_id = api.upload_image_id(encode(halves((64, 48)), "JPEG"))
    resp = api.get_image(image_id, "thumb")

    assert resp.headers["content-security-policy"] == "default-src 'none'; sandbox"
    api.delete_image(image_id)


# -- sessions -------------------------------------------------------------------


def signed_in(base: str, cookies: httpx.Cookies) -> bool:
    """Whether a client holding exactly these cookies gets the diary."""
    with httpx.Client(base_url=base, cookies=cookies, timeout=30.0) as client:
        resp = client.get("/d/today")
        return resp.status_code == 200 and "data-csrf-token" in resp.text


def copy_cookies(client: httpx.Client) -> httpx.Cookies:
    copied = httpx.Cookies()
    for cookie in client.cookies.jar:
        copied.set(cookie.name, cookie.value or "", domain=cookie.domain)
    return copied


@pytest.mark.parametrize("path", ["/login", "/register", "/reset"])
def test_sign_in_forms_need_the_csrf_token(live_server: LiveServer, path: str) -> None:
    resp = httpx.post(
        live_server.url + path,
        data={"username": "someone", "password": "x", "recovery_code": "x"},
        timeout=30.0,
    )
    assert resp.status_code == 400


def test_a_logged_out_cookie_is_worthless(
    live_server: LiveServer, make_user: Callable[..., User]
) -> None:
    api = ApiClient.logged_in(live_server.url, make_user("logout"))
    stolen = copy_cookies(api.http)
    assert signed_in(live_server.url, stolen)

    api.http.post("/logout", headers=api.headers)
    api.close()

    assert not signed_in(live_server.url, stolen)


def test_changing_the_password_signs_out_other_sessions(
    live_server: LiveServer, make_user: Callable[..., User]
) -> None:
    user = make_user("pwchange")
    here = ApiClient.logged_in(live_server.url, user)
    elsewhere = ApiClient.logged_in(live_server.url, user)
    other_cookies = copy_cookies(elsewhere.http)
    new_password = user.password + "-renewed"

    resp = here.http.post(
        "/profile/password",
        data={
            "current_password": user.password,
            "new_password": new_password,
            "confirm_password": new_password,
        },
        headers=here.headers,
    )
    assert resp.status_code == 200 and "pw_success" not in resp.text

    # The session that changed it stays; any other (maybe an attacker's) ends.
    assert signed_in(live_server.url, copy_cookies(here.http))
    assert not signed_in(live_server.url, other_cookies)
    here.close()
    elsewhere.close()


def test_resetting_with_the_recovery_code_signs_out_every_session(
    live_server: LiveServer, make_user: Callable[..., User]
) -> None:
    user = make_user("reset")
    api = ApiClient.logged_in(live_server.url, user)
    old_cookies = copy_cookies(api.http)
    new_password = user.password + "-reset"

    with httpx.Client(base_url=live_server.url, timeout=30.0) as client:
        form = client.get("/reset")
        token = _CSRF_INPUT_RE.search(form.text)
        assert token
        resp = client.post(
            "/reset",
            data={
                "username": user.username,
                "recovery_code": user.recovery_code,
                "new_password": new_password,
                "confirm_password": new_password,
                "csrf_token": token.group(1),
            },
        )
        assert resp.status_code in (302, 303), resp.text[:300]

    assert not signed_in(live_server.url, old_cookies)
    api.close()


def test_a_deleted_account_cookie_is_worthless(
    live_server: LiveServer, make_user: Callable[..., User]
) -> None:
    api = ApiClient.logged_in(live_server.url, make_user("gone"))
    stolen = copy_cookies(api.http)
    assert api.http.delete("/profile", headers=api.headers).status_code == 204
    api.close()

    assert not signed_in(live_server.url, stolen)


def test_the_session_cookie_is_http_only_and_same_site(
    live_server: LiveServer, make_user: Callable[..., User]
) -> None:
    resp = post_login(live_server.url, make_user("cookie"), "/")

    cookie = next(
        c for c in resp.headers.get_list("set-cookie") if c.startswith("llamora=")
    )
    flags = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert "httponly" in flags
    assert "samesite=lax" in flags
