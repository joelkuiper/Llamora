"""Helpers for date/time tests: pinned clocks, zones and prompt inspection.

The app's intended model of time (see test_dates_*.py):

* "Today" is the writer's local calendar day, as reported by the browser
  (``X-Client-Today`` / ``client_today`` and the IANA zone). The server's own
  clock, in the client's zone, is only a fallback when the client hasn't said.
* Only today's page is writable in the UI; edits are enforced server-side.
* One day opening per user per day, for the client's today only.
* A page showing today moves to the new day at local midnight, carrying any
  unsent draft along.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import Page

from fake_llm import FakeLLM
from harness import ApiClient, User

_PROMPT_DAY_RE = re.compile(r"Today is the (.+?), during the ([\w-]+)\.")


@dataclass(slots=True)
class Diary:
    """A logged-in browser page for a fresh user, with a pinned zone/clock."""

    page: Page
    user: User
    api: ApiClient
    base_url: str
    tz: str


def at(
    tz: str,
    year: int,
    month: int,
    day: int,
    hour: int = 12,
    minute: int = 0,
    second: int = 0,
) -> datetime:
    """An aware local datetime in ``tz``."""
    return datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo(tz))


def long_date(day: date) -> str:
    """The app's prose date, e.g. '29th of March 2026'."""
    n = day.day
    suffix = (
        "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix} of {day.strftime('%B %Y')}"


def rendered_day(page: Page) -> str:
    """The date of the entries currently on screen."""
    return page.locator("#entries").get_attribute("data-date") or ""


def _messages_text(request: dict[str, Any]) -> str:
    return "\n".join(str(m.get("content", "")) for m in request.get("messages") or [])


def opening_requests(fake_llm: FakeLLM) -> list[dict[str, Any]]:
    """Chat requests that generate a day opening (their user turn is the recap)."""
    return [
        request
        for request in fake_llm.chat_requests(structured=False)
        if any(
            m.get("role") == "user"
            and str(m.get("content", "")).startswith("Yesterday recap")
            for m in request.get("messages") or []
        )
    ]


def reply_requests(fake_llm: FakeLLM) -> list[dict[str, Any]]:
    """Chat requests that answer an entry (anything streamed that isn't an opening)."""
    openings = {id(r) for r in opening_requests(fake_llm)}
    return [
        r for r in fake_llm.chat_requests(structured=False) if id(r) not in openings
    ]


def prompt_day(request: dict[str, Any]) -> tuple[str, str] | None:
    """The (date, part of day) the model is told, e.g. ('29th of March 2026', 'morning')."""
    match = _PROMPT_DAY_RE.search(_messages_text(request))
    return (match.group(1), match.group(2)) if match else None


def prompt_text(request: dict[str, Any]) -> str:
    return _messages_text(request)
