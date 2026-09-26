"""Server and browser disagreeing about the date or zone.

Intended behaviour: the writer's browser wins. The server's clock (a
dedicated server with a pinned clock here) is only a fallback, and bad or
stale zone information degrades gracefully instead of breaking pages.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from urllib.parse import quote

import pytest
from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import wait_for_app, write_entry
from timekit import Diary, at, long_date, opening_requests, prompt_day, reply_requests

DiaryAt = Callable[..., Diary]


@pytest.mark.parametrize(
    ("tz", "server_now", "client_day", "part"),
    [
        # Server just past UTC midnight; Los Angeles (PDT since 8 March) is
        # still on the 10th, at 17:30.
        (
            "America/Los_Angeles",
            at("UTC", 2026, 3, 11, 0, 30),
            date(2026, 3, 10),
            "evening",
        ),
        # Server still on the 10th (23:30 UTC); Tokyo is on the 11th (08:30).
        ("Asia/Tokyo", at("UTC", 2026, 3, 10, 23, 30), date(2026, 3, 11), "morning"),
    ],
    ids=["server-ahead", "server-behind"],
)
def test_the_writers_day_wins_over_the_servers(
    diary_at: DiaryAt,
    fake_llm: FakeLLM,
    tz: str,
    server_now,
    client_day: date,
    part: str,
) -> None:
    diary = diary_at(tz=tz, at=server_now, server_now=server_now)
    page = diary.page

    expect(page.locator("#entries")).to_have_attribute(
        "data-date", client_day.isoformat()
    )
    expect(page.locator("#calendar-label")).to_have_text(long_date(client_day))
    assert [prompt_day(r) for r in opening_requests(fake_llm)] == [
        (long_date(client_day), part)
    ]

    entry = write_entry(page, "Written across the date line")
    entry.get_by_role("button", name="Respond").click()
    wait_for_app(page)
    assert prompt_day(reply_requests(fake_llm)[-1]) == (long_date(client_day), part)

    page.reload()
    wait_for_app(page)
    expect(page.locator("#entries")).to_have_attribute(
        "data-date", client_day.isoformat()
    )
    expect(page.locator("#entries .entry.user", has_text="date line")).to_be_visible()


def test_stale_timezone_cookie_after_travelling(
    diary_at: DiaryAt, open_page: Callable[..., Page], tmp_path
) -> None:
    # Logged in from Los Angeles; later the same browser session is in Tokyo,
    # where it is already the next day. The tz cookie still says Los Angeles.
    moment = at("America/Los_Angeles", 2026, 3, 10, 20)  # 12:00 on the 11th in Tokyo
    diary = diary_at(tz="America/Los_Angeles", at=moment, server_now=moment)
    state = tmp_path / "state.json"
    diary.page.context.storage_state(path=str(state))

    tokyo = open_page(
        base_url=diary.base_url, tz="Asia/Tokyo", at=moment, storage_state=str(state)
    )
    tokyo.goto("/d/today")
    wait_for_app(tokyo)

    expect(tokyo.locator("#entries")).to_have_attribute("data-date", "2026-03-11")
    write_entry(tokyo, "Landed in Tokyo")
    tokyo.reload()
    wait_for_app(tokyo)
    expect(
        tokyo.locator("#entries .entry.user", has_text="Landed in Tokyo")
    ).to_be_visible()
    cookie = {c["name"]: c["value"] for c in tokyo.context.cookies()}["tz"]
    assert cookie in {"Asia/Tokyo", quote("Asia/Tokyo", safe="")}


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Timezone": "Mars/Olympus_Mons"},
        {"X-Timezone": ""},
        {"X-Client-Today": "2026-02-30"},
        {"X-Client-Today": "yesterday"},
    ],
    ids=["unknown-zone", "empty-zone", "impossible-date", "not-a-date"],
)
def test_bad_date_information_falls_back_gracefully(diary_at: DiaryAt, headers) -> None:
    diary = diary_at()
    client = diary.api._client

    for path in ("/d/today", "/e/today", "/calendar"):
        resp = client.get(path, headers={"HX-Request": "true", **headers})
        assert resp.status_code == 200, f"{path} with {headers} -> {resp.status_code}"


def test_percent_encoded_zone_cookie_is_understood(diary_at: DiaryAt) -> None:
    # Cookies may arrive percent-encoded ("Europe%2FAmsterdam").
    moment = at("UTC", 2026, 3, 10, 23, 30)  # 00:30 on the 11th in Amsterdam
    diary = diary_at(tz="UTC", at=moment, server_now=moment)
    client = diary.api._client
    host = client.base_url.host
    client.cookies.delete("tz")  # replace the session's own tz cookie
    client.cookies.set("tz", "Europe%2FAmsterdam", domain=host)

    resp = client.get("/d/today")

    assert resp.status_code == 200
    assert 'data-date="2026-03-11"' in resp.text
