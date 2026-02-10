"""Generate realistic end-to-end demo data via the running Llamora server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import textwrap
from collections import deque
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

import httpx
import orjson
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

from llamora.settings import settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DemoConfig:
    base_url: str
    username: str
    password: str
    start_date: date
    end_date: date
    min_entries: int
    max_entries: int
    open_day_rate: float
    open_only_rate: float
    response_rate: float
    max_tags: int
    tz: str
    seed: int
    persona_hint: str
    llm_max_tokens: int
    markdown_rate: float
    multi_response_rate: float
    max_responses_per_entry: int
    min_entry_chars: int
    max_entry_chars: int
    entry_retries: int
    entry_context_size: int
    entry_context_chars: int
    entry_temperature: float | None
    empty_day_rate: float
    story_events: int
    story_followup_rate: float
    story_intensity: float
    story_allow_overlap: bool


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _iter_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _select_csrf_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    token_el = soup.select_one('input[name="csrf_token"]')
    if token_el and token_el.has_attr("value"):
        return token_el["value"]
    body = soup.select_one("body")
    if body and body.has_attr("data-csrf-token"):
        return body["data-csrf-token"]
    return None


def _select_body_csrf(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("body")
    if body and body.has_attr("data-csrf-token"):
        return body["data-csrf-token"]
    return None


def _select_entry_id(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    entry = soup.select_one(".entry[data-entry-id]")
    if entry and entry.has_attr("data-entry-id"):
        return entry["data-entry-id"]
    entry = soup.select_one("div[id^='entry-']")
    if entry and entry.has_attr("id"):
        entry_id = entry["id"].replace("entry-", "", 1)
        return entry_id or None
    match = re.search(r'data-entry-id=["\\\']([^"\\\']+)["\\\']', html)
    if match:
        return match.group(1)
    match = re.search(r'id=["\\\']entry-([^"\\\']+)["\\\']', html)
    if match:
        return match.group(1)
    return None


def _select_tag_suggestions(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    tags: list[str] = []
    for btn in soup.select(".tag-suggestion"):
        value = btn.get("data-tag") or btn.text
        value = (value or "").strip()
        if value and value not in tags:
            tags.append(value)
    return tags


def _is_login_page(html: str, url: str | None = None) -> bool:
    if url and "/login" in url:
        return True
    if "<title>Llamora | Login</title>" in html:
        return True
    return False


def _load_response_kinds() -> list[str]:
    raw = settings.get("LLM.response_kinds", []) or []
    kinds: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind_id = str(entry.get("id") or "").strip()
        if kind_id:
            kinds.append(kind_id)
    if not kinds:
        kinds = ["reply"]
    return kinds


def _extract_auth_error(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    message = soup.select_one(".alert__message")
    if message:
        text = message.get_text(strip=True)
        return text or None
    return None


def _has_session_cookie(client: httpx.AsyncClient) -> bool:
    cookie_name = str(settings.get("COOKIES.name") or "llamora")
    return client.cookies.get(cookie_name) is not None


def _strip_outer_quotes(text: str) -> str:
    if not text:
        return text
    stripped = text.strip()
    pairs = [
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
    ]
    for left, right in pairs:
        if stripped.startswith(left) and stripped.endswith(right) and len(stripped) > 2:
            return stripped[1:-1].strip()
    return text


def _log_wrapped(prefix: str, text: str, width: int = 100) -> None:
    if not text:
        return
    wrapped = textwrap.wrap(text, width=width) or [text]
    for idx, line in enumerate(wrapped):
        if idx == 0:
            logger.info("%s%s", prefix, line)
        else:
            logger.info("%s%s", " " * len(prefix), line)


@dataclass(slots=True)
class NarrativeEvent:
    date: date
    title: str
    theme: str
    beats: list[str]
    tone: str
    followup_days: list[int]


def _parse_event_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


async def _generate_narrative_timeline(
    llm: AsyncOpenAI,
    config: DemoConfig,
) -> dict[date, list[NarrativeEvent]]:
    if config.story_events <= 0:
        return {}
    rng = random.Random(config.seed)
    all_days = list(_iter_days(config.start_date, config.end_date))
    if not all_days:
        return {}
    count = min(config.story_events, len(all_days))
    event_days = sorted(rng.sample(all_days, count))
    date_lines = "\n".join(f"- {d.isoformat()}" for d in event_days)
    prompt = (
        "Create a realistic life timeline for one person writing a diary. "
        "Return strict JSON array only, no extra text. Each item must include: "
        "date (YYYY-MM-DD), title, theme (1-3 words), beats (1-3 short items), "
        "emotional_tone, followup_days (optional list of integers like 1,2,3). "
        "Keep events plausible and varied; avoid extremes or drama. "
        "Use the provided dates exactly and keep the order."
    )
    user_message = (
        f"Start date: {config.start_date.isoformat()}\n"
        f"End date: {config.end_date.isoformat()}\n"
        f"Count: {count}\n"
        f"Followup chance: {config.story_followup_rate}\n"
        f"Event dates (use exactly, keep order):\n{date_lines}\n\n"
        f"{prompt}"
    )
    model = settings.get("LLM.chat.model") or "local"
    params = dict(settings.get("LLM.chat.parameters") or {})
    params["temperature"] = min(
        0.7, max(0.1, float(params.get("temperature", 0.4)))
    )
    params["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "timeline_events",
            "strict": True,
            "schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "title": {"type": "string"},
                        "theme": {"type": "string"},
                        "beats": {"type": "array", "items": {"type": "string"}},
                        "emotional_tone": {"type": "string"},
                        "followup_days": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": [
                        "date",
                        "title",
                        "theme",
                        "beats",
                        "emotional_tone",
                    ],
                    "additionalProperties": False,
                },
            },
        },
    }
    response = await llm.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You design personal timelines. Return only JSON, no code, no markdown."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        max_tokens=max(600, int(config.llm_max_tokens)),
        **params,
    )
    raw = (response.choices[0].message.content or "").strip()
    try:
        import orjson

        data = orjson.loads(raw)
    except Exception:
        logger.warning("Failed to parse timeline JSON; skipping narrative scaffold")
        logger.info("Timeline raw (truncated): %s", raw[:1200])
        return {}
    if not isinstance(data, list):
        logger.warning("Timeline JSON is not a list; skipping narrative scaffold")
        return {}

    events_by_date: dict[date, list[NarrativeEvent]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        d = _parse_event_date(str(item.get("date") or ""))
        if d is None:
            continue
        title = str(item.get("title") or "").strip()
        theme = str(item.get("theme") or "").strip()
        tone = str(item.get("emotional_tone") or "").strip()
        beats_raw = item.get("beats") or []
        beats: list[str] = []
        if isinstance(beats_raw, list):
            for beat in beats_raw:
                b = str(beat or "").strip()
                if b:
                    beats.append(b)
        followups_raw = item.get("followup_days") or []
        followups: list[int] = []
        if isinstance(followups_raw, list):
            for val in followups_raw:
                try:
                    followups.append(int(val))
                except Exception:
                    continue
        if not followups and random.random() < config.story_followup_rate:
            followups = random.sample([1, 2, 3], k=1)
        if not title and beats:
            title = beats[0][:40]
        event = NarrativeEvent(
            date=d,
            title=title,
            theme=theme,
            beats=beats,
            tone=tone,
            followup_days=followups,
        )
        events_by_date.setdefault(d, []).append(event)
        for offset in followups:
            follow_date = d + timedelta(days=offset)
            events_by_date.setdefault(follow_date, []).append(event)

    if not config.story_allow_overlap:
        for d, events in list(events_by_date.items()):
            if len(events) <= 1:
                continue
            events_by_date[d] = [events[0]]
    return events_by_date


def _resolve_llm_base_url() -> str:
    base_url = settings.get("LLM.chat.base_url")
    if base_url:
        return str(base_url).rstrip("/")
    host = settings.get("LLM.upstream.host") or ""
    host = str(host).strip().rstrip("/")
    if not host:
        raise RuntimeError("LLM.chat.base_url or LLM.upstream.host must be set")
    return f"{host}/v1"


def _build_llm_client() -> AsyncOpenAI:
    base_url = _resolve_llm_base_url()
    api_key = settings.get("LLM.chat.api_key") or "local"
    timeout = float(settings.get("LLM.chat.timeout_seconds") or 30.0)
    max_retries = int(settings.get("LLM.chat.max_retries") or 0)
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
    )


def _choose_entry_times(count: int, day: date) -> list[datetime]:
    if count <= 0:
        return []
    slots = [(8, 11), (12, 15), (19, 23)]
    weights = [0.35, 0.2, 0.45]
    times: list[datetime] = []
    for _ in range(count):
        window = random.choices(slots, weights=weights, k=1)[0]
        hour = random.randint(window[0], window[1])
        minute = random.choice([0, 5, 10, 15, 20, 25, 30, 40, 50])
        second = random.randint(0, 50)
        dt = datetime.combine(day, time(hour, minute, second), tzinfo=timezone.utc)
        times.append(dt)
    times.sort()
    return times


async def _generate_entry_text(
    llm: AsyncOpenAI,
    config: DemoConfig,
    entry_date: date,
    entry_time: datetime,
    index: int,
    include_markdown: bool,
    recent_entries: list[str],
    event_note: str | None,
    followup_note: str | None,
) -> str:
    persona = config.persona_hint.strip()
    time_label = entry_time.strftime("%H:%M")

    # Soft length variation: sometimes short, sometimes longer.
    length_mode = random.choices(
        ["short", "medium", "long"],
        weights=[0.45, 0.35, 0.20],
        k=1,
    )[0]
    length_hint = ""
    if length_mode == "medium":
        length_hint = "Keep rambling.\n"
    elif length_mode == "long":
        length_hint = "Write a short essay on a topic.\n"

    user_message = (
        "Context (do NOT include this information in the entry):\n"
        f"- Date: {entry_date.isoformat()}\n"
        f"- Time: {time_label}\n"
        f"- Entry number today: {index + 1}\n"
        f"- Persona: {persona}\n\n"
        "You are using a journaling / diary app to write a personal note for yourself.\n"
        "Write the note directly, without restating the date, time, entry number, or persona.\n"
        "Use normal, everyday language.\n"
        "Do not add a title.\n"
        "Do not wrap the entry in quotation marks or start with a quote.\n"
        "Do not try to summarize ongoing worries unless something actually changed.\n"
        "It is fine to write about only one small thing, or nothing important at all.\n"
        f"{length_hint}"
    )
    if event_note:
        user_message += f"\nEvent note (do not quote): {event_note}\n"
    if followup_note:
        user_message += f"\nRecent event context (lightly reflect on it): {followup_note}\n"

    if include_markdown:
        user_message += (
            "\nYou may use a small amount of markdown if it feels natural "
            "(for example italics within a sentence)."
        )

    # Context dropout + thinning: not every entry gets continuity, and we only
    # include 1–2 recent entries when we do.
    continuity_message = None
    if recent_entries and random.random() < 0.7:  # 30% chance: no context
        max_entries = random.choice([1, 2])
        chosen = recent_entries[-max_entries:]
        snippet_chars = max(64, int(config.entry_context_chars))
        snippets = "\n".join(
            f"- {entry[:snippet_chars].strip()}"
            for entry in chosen
            if entry.strip()
        )
        continuity_message = (
            "Previous entries (reference only; do not copy wording or structure):\n"
            f"{snippets}"
        )

    model = settings.get("LLM.chat.model") or "local"
    params = dict(settings.get("LLM.chat.parameters") or {})
    if config.entry_temperature is not None:
        params["temperature"] = float(config.entry_temperature)

    attempts = max(1, int(config.entry_retries))
    for _ in range(attempts):
        messages = [
            {"role": "system", "content": "Generate a realistic personal note."},
            {"role": "user", "content": user_message},
        ]
        if continuity_message:
            messages.append({"role": "user", "content": continuity_message})

        response = await llm.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=config.llm_max_tokens,
            **params,
        )

        text = (response.choices[0].message.content or "").strip()
        if not text:
            continue
        text = _strip_outer_quotes(text)

        if len(text) < config.min_entry_chars:
            extra = (
                "You can expand slightly on the same situation.\n"
                if length_mode != "short"
                else ""
            )
            user_message = (
                f"{user_message}\n\n"
                f"Write a bit more (at least {config.min_entry_chars} characters).\n"
                f"{extra}"
                "Do not add metadata or repeat earlier concerns unless needed."
            )
            continue

        if len(text) > config.max_entry_chars:
            text = text[: config.max_entry_chars].rsplit(" ", 1)[0].rstrip()

        return text

    return text.strip() if "text" in locals() else ""


async def _consume_sse(stream: httpx.Response) -> str:
    event = None
    data: list[str] = []
    messages: list[str] = []
    async for line in stream.aiter_lines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            data.append(payload)
        elif not line:
            if event == "message" and data:
                chunk = "".join(data).replace("[newline]", "\n")
                messages.append(chunk)
            if event == "done":
                return "".join(messages)
            event = None
            data = []
    return "".join(messages)


async def _register_user(client: httpx.AsyncClient, config: DemoConfig) -> bool:
    logger.debug("Registering user %s", config.username)
    resp = await client.get("/register")
    resp.raise_for_status()
    csrf = _select_csrf_from_html(resp.text)
    if not csrf:
        raise RuntimeError("Failed to locate CSRF token on /register")

    data = {
        "username": config.username,
        "password": config.password,
        "confirm_password": config.password,
        "csrf_token": csrf,
    }
    resp = await client.post("/register", data=data)
    if resp.status_code >= 400:
        logger.warning("Register failed with status %s", resp.status_code)
        return False

    if "Recovery Code" in resp.text:
        logger.info("Recovery screen received; user created")
        return True

    error = _extract_auth_error(resp.text)
    if error:
        logger.warning("Register failed: %s", error)
        return False
    logger.warning("Register response did not include recovery screen; treating as failure")
    return False


async def _login_user(client: httpx.AsyncClient, config: DemoConfig) -> None:
    logger.debug("Logging in as %s", config.username)
    resp = await client.get("/login")
    resp.raise_for_status()
    csrf = _select_csrf_from_html(resp.text)
    if not csrf:
        raise RuntimeError("Failed to locate CSRF token on /login")

    data = {
        "username": config.username,
        "password": config.password,
        "csrf_token": csrf,
    }
    resp = await client.post("/login", data=data)
    resp.raise_for_status()
    if _is_login_page(resp.text, str(resp.url)):
        error = _extract_auth_error(resp.text)
        raise RuntimeError(f"Login failed: {error or 'unexpected login response'}")
    if not _has_session_cookie(client):
        raise RuntimeError(
            "Login succeeded but no session cookie was set. "
            "Check LLAMORA_COOKIES__FORCE_SECURE and base URL scheme."
        )


async def _login_and_refresh_csrf(client: httpx.AsyncClient, config: DemoConfig) -> str:
    await _login_user(client, config)
    return await _refresh_csrf(client)


async def _refresh_csrf(client: httpx.AsyncClient) -> str:
    candidates = ["/", "/d/today", "/login"]
    for path in candidates:
        resp = await client.get(path)
        if resp.status_code >= 400:
            logger.warning("CSRF refresh failed for %s (status=%s)", path, resp.status_code)
            continue
        csrf = _select_body_csrf(resp.text) or _select_csrf_from_html(resp.text)
        if csrf:
            logger.debug("CSRF token loaded from %s", path)
            return csrf
        logger.debug("CSRF token not found on %s (url=%s)", path, str(resp.url))
    raise RuntimeError("Failed to locate CSRF token from known pages")


async def _open_day(client: httpx.AsyncClient, day: date, headers: dict[str, str]) -> None:
    logger.debug("Opening day %s", day)
    resp = await client.get(f"/e/{day.isoformat()}", headers=headers)
    resp.raise_for_status()


async def _open_day_opening(client: httpx.AsyncClient, day: date, headers: dict[str, str]) -> None:
    logger.debug("Opening day opening stream %s", day)
    async with client.stream("GET", f"/e/opening/{day.isoformat()}", headers=headers) as stream:
        await _consume_sse(stream)


async def _create_entry(
    client: httpx.AsyncClient,
    day: date,
    text: str,
    user_time: datetime,
    headers: dict[str, str],
    config: DemoConfig,
) -> str:
    logger.debug("Posting entry for %s", day)
    data = {
        "text": text,
        "user_time": user_time.isoformat(),
    }
    resp = await client.post(f"/e/{day.isoformat()}/entry", data=data, headers=headers)
    if _is_login_page(resp.text, str(resp.url)):
        logger.warning("Session expired while posting entry; re-authenticating")
        csrf = await _login_and_refresh_csrf(client, config)
        headers["X-CSRFToken"] = csrf
        resp = await client.post(f"/e/{day.isoformat()}/entry", data=data, headers=headers)
    resp.raise_for_status()
    entry_id = _select_entry_id(resp.text)
    if not entry_id:
        logger.error(
            "Unable to parse entry id from response (status=%s, length=%s)",
            resp.status_code,
            len(resp.text),
        )
        logger.debug("Entry response body (truncated): %s", resp.text[:1200])
        raise RuntimeError("Unable to parse entry id from response")
    return entry_id


async def _trigger_response(
    client: httpx.AsyncClient,
    day: date,
    entry_id: str,
    user_time: datetime,
    headers: dict[str, str],
    response_kind: str,
) -> None:
    logger.debug("Triggering response for entry %s", entry_id)
    data = {
        "user_time": user_time.isoformat(),
        "response_kind": response_kind,
    }
    resp = await client.post(f"/e/{day.isoformat()}/response/{entry_id}", data=data, headers=headers)
    resp.raise_for_status()

    async with client.stream(
        "GET",
        f"/e/{day.isoformat()}/response/stream/{entry_id}",
        headers=headers,
    ) as stream:
        response_text = await _consume_sse(stream)
    if response_text:
        response_text = _strip_outer_quotes(response_text)
        _log_wrapped(
            f"  assistant ({response_kind}): ",
            response_text.strip(),
        )


async def _select_tags_with_llm(
    llm: AsyncOpenAI,
    config: DemoConfig,
    entry_text: str,
    suggestions: list[str],
    max_tags: int,
) -> list[str]:
    if not suggestions or max_tags <= 0:
        return []
    # Bias toward fewer tags to keep outputs realistic.
    if random.random() < 0.25:
        return []
    if random.random() < 0.35:
        max_tags = min(1, max_tags)
    elif random.random() < 0.35:
        max_tags = min(2, max_tags)
    model = settings.get("LLM.chat.model") or "local"
    params = dict(settings.get("LLM.chat.parameters") or {})
    params["temperature"] = min(0.4, max(0.1, float(params.get("temperature", 0.2))))
    params["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "tag_choice",
            "strict": True,
            "schema": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": max_tags,
            },
        },
    }
    tag_lines = "\n".join(f"- {tag}" for tag in suggestions)
    user_message = (
        "Pick the most relevant tags for the entry from the suggestions list. "
        "Return a JSON array only (no markdown, no code fences). Choose up to "
        f"{max_tags} tags. If none fit, return an empty list.\n\n"
        "Entry:\n"
        f"{entry_text}\n\n"
        "Suggestions:\n"
        f"{tag_lines}"
    )
    response = await llm.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You select tags for diary entries. "
                    "Output a raw JSON array only. No markdown or code fences."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        max_tokens=200,
        **params,
    )
    raw = (response.choices[0].message.content or "").strip()
    try:
        parsed = orjson.loads(raw)
    except Exception:
        logger.info("Tag selection raw (truncated): %s", raw[:1200])
        logger.warning("Failed to parse tag selection JSON; skipping tags")
        return []
    tags = parsed if isinstance(parsed, list) else None
    if not isinstance(tags, list):
        return []
    allowed = {tag.strip() for tag in suggestions}
    selected: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if not value or value not in allowed or value in selected:
            continue
        selected.append(value)
        if len(selected) >= max_tags:
            break
    return selected


async def _apply_tags(
    llm: AsyncOpenAI,
    config: DemoConfig,
    client: httpx.AsyncClient,
    entry_id: str,
    entry_text: str,
    max_tags: int,
    headers: dict[str, str],
) -> None:
    resp = await client.get(f"/t/suggestions/{entry_id}", headers=headers)
    resp.raise_for_status()
    suggestions = _select_tag_suggestions(resp.text)
    if not suggestions:
        logger.debug("No tag suggestions for %s", entry_id)
        return

    selected = await _select_tags_with_llm(
        llm, config, entry_text, suggestions, max_tags
    )
    if not selected:
        return
    if selected:
        logger.info("    tags: %s", ", ".join(selected))
    for tag in selected:
        logger.debug("Adding tag '%s' to %s", tag, entry_id)
        data = {"tag": tag}
        resp = await client.post(f"/t/{entry_id}", data=data, headers=headers)
        resp.raise_for_status()


async def generate_dataset(config: DemoConfig) -> None:
    random.seed(config.seed)
    llm = _build_llm_client()
    response_kinds = _load_response_kinds()
    logger.info("User: %s", config.username)
    logger.info("Range: %s to %s (%s)", config.start_date, config.end_date, config.tz)
    logger.info("Persona: %s", config.persona_hint)
    logger.debug("Response kinds: %s", ", ".join(response_kinds))
    recent_entries: deque[str] = deque(maxlen=max(0, config.entry_context_size))
    narrative_events = await _generate_narrative_timeline(llm, config)
    if narrative_events:
        logger.info("-" * 72)
        logger.info("Narrative scaffold")
        for day in sorted(narrative_events.keys()):
            for event in narrative_events[day]:
                when = day.isoformat()
                title = event.title or event.theme or "event"
                detail = event.beats[0] if event.beats else event.tone
                logger.info("  %s: %s", when, title)
                if detail:
                    logger.info("    - %s", detail)
        logger.info("-" * 72)

    async with httpx.AsyncClient(
        base_url=config.base_url,
        follow_redirects=True,
        timeout=httpx.Timeout(60.0),
    ) as client:
        registered = await _register_user(client, config)
        if registered:
            logger.info("Registration complete; logging in to establish session")
        csrf = await _login_and_refresh_csrf(client, config)
        base_headers = {
            "X-CSRFToken": csrf,
            "X-Timezone": config.tz,
        }

        total_entries = 0
        opened_any_day = False
        for day in _iter_days(config.start_date, config.end_date):
            logger.info("-" * 72)
            logger.info("Day %s", day.isoformat())
            headers = {
                **base_headers,
                "X-Client-Today": day.isoformat(),
            }
            if random.random() < config.empty_day_rate:
                logger.info("  empty day")
                continue
            event_note = None
            followup_note = None
            events_today = narrative_events.get(day) or []
            if events_today:
                event = events_today[0]
                if event.date == day:
                    detail = event.beats[0] if event.beats else event.title
                    if random.random() < config.story_intensity:
                        event_note = f"{event.title}. {detail}. tone: {event.tone}."
                    else:
                        event_note = f"{event.theme} / {event.tone}."
                    logger.info("  event: %s", event.title or event.theme)
                else:
                    followup_note = f"{event.title} ({event.tone})"
            open_day = True
            open_only = random.random() < config.open_only_rate

            if open_day:
                logger.info("  opening: yes")
                await _open_day(client, day, headers)
                await _open_day_opening(client, day, headers)
                opened_any_day = True

            if open_only:
                logger.info("  entries: 0 (opening only)")
                continue

            entries_today = random.randint(config.min_entries, config.max_entries)
            if entries_today == 0:
                logger.info("  entries: 0")
                continue

            times = _choose_entry_times(entries_today, day)
            logger.info("  entries: %s", entries_today)
            for idx, entry_time in enumerate(times):
                logger.debug("Generating entry %s/%s for %s", idx + 1, entries_today, day)
                include_markdown = random.random() < config.markdown_rate
                text = await _generate_entry_text(
                    llm,
                    config,
                    day,
                    entry_time,
                    idx,
                    include_markdown,
                    list(recent_entries),
                    event_note,
                    followup_note,
                )
                if text:
                    logger.info("  entry %s/%s @ %s", idx + 1, entries_today, entry_time.strftime("%H:%M"))
                    _log_wrapped("    ", text.strip())
                    recent_entries.append(text)
                entry_id = await _create_entry(client, day, text, entry_time, headers, config)
                total_entries += 1

                if random.random() < config.response_rate:
                    response_count = 1
                    if (
                        config.max_responses_per_entry > 1
                        and random.random() < config.multi_response_rate
                    ):
                        response_count = random.randint(2, config.max_responses_per_entry)
                    last_kind = None
                    for _ in range(response_count):
                        response_kind = random.choice(response_kinds)
                        if last_kind and len(response_kinds) > 1:
                            while response_kind == last_kind:
                                response_kind = random.choice(response_kinds)
                        last_kind = response_kind
                        await _trigger_response(
                            client,
                            day,
                            entry_id,
                            entry_time,
                            headers,
                            response_kind,
                        )

                await _apply_tags(llm, config, client, entry_id, text, config.max_tags, headers)

        logger.info("Done. Created %d entries.", total_entries)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Llamora demo data")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--min-entries", type=int, default=0)
    parser.add_argument("--max-entries", type=int, default=3)
    parser.add_argument("--open-day-rate", type=float, default=0.6)
    parser.add_argument("--open-only-rate", type=float, default=0.15)
    parser.add_argument(
        "--empty-day-rate",
        type=float,
        default=0.1,
        help="Probability a day has no opening and no entries.",
    )
    parser.add_argument("--response-rate", type=float, default=0.6)
    parser.add_argument("--max-tags", type=int, default=4)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument(
        "--persona",
        default=(
            "A calm, reflective writer who notices small shifts in mood, light, and place. "
            "Often references nature, memory, and presence. "
        ),
        help="Persona hint used to keep entries consistent. Override to change voice.",
    )
    parser.add_argument("--llm-max-tokens", type=int, default=280)
    parser.add_argument(
        "--min-entry-chars",
        type=int,
        default=260,
        help="Minimum character length for generated entries.",
    )
    parser.add_argument(
        "--max-entry-chars",
        type=int,
        default=1200,
        help="Maximum character length for generated entries.",
    )
    parser.add_argument(
        "--entry-retries",
        type=int,
        default=2,
        help="Retries for entry generation if too short.",
    )
    parser.add_argument(
        "--entry-context-size",
        type=int,
        default=4,
        help="Number of recent entries to include for continuity.",
    )
    parser.add_argument(
        "--entry-context-chars",
        type=int,
        default=2048,
        help="Max characters per recent entry snippet for continuity.",
    )
    parser.add_argument(
        "--entry-temperature",
        type=float,
        default=None,
        help="Override temperature for entry generation.",
    )
    parser.add_argument(
        "--story-events",
        type=int,
        default=6,
        help="Number of narrative events to scaffold across the date range.",
    )
    parser.add_argument(
        "--story-followup-rate",
        type=float,
        default=0.4,
        help="Probability an event includes follow-up days.",
    )
    parser.add_argument(
        "--story-intensity",
        type=float,
        default=0.6,
        help="How strongly to inject event details into an entry.",
    )
    parser.add_argument(
        "--story-allow-overlap",
        action="store_true",
        help="Allow multiple events on the same date.",
    )
    parser.add_argument(
        "--markdown-rate",
        type=float,
        default=0.3,
        help="Probability an entry includes a subtle markdown element.",
    )
    parser.add_argument(
        "--multi-response-rate",
        type=float,
        default=0.2,
        help="Probability of generating multiple responses for one entry.",
    )
    parser.add_argument(
        "--max-responses-per-entry",
        type=int,
        default=2,
        help="Upper bound on responses per entry when multi-response triggers.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


async def _main_async() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        if args.verbose
        else "%(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    cfg = DemoConfig(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
        min_entries=args.min_entries,
        max_entries=args.max_entries,
        open_day_rate=args.open_day_rate,
        open_only_rate=args.open_only_rate,
        response_rate=args.response_rate,
        max_tags=args.max_tags,
        tz=args.timezone,
        seed=args.seed,
        persona_hint=args.persona,
        llm_max_tokens=args.llm_max_tokens,
        markdown_rate=args.markdown_rate,
        multi_response_rate=args.multi_response_rate,
        max_responses_per_entry=args.max_responses_per_entry,
        min_entry_chars=args.min_entry_chars,
        max_entry_chars=args.max_entry_chars,
        entry_retries=args.entry_retries,
        entry_context_size=args.entry_context_size,
        entry_context_chars=args.entry_context_chars,
        entry_temperature=args.entry_temperature,
        empty_day_rate=args.empty_day_rate,
        story_events=args.story_events,
        story_followup_rate=args.story_followup_rate,
        story_intensity=args.story_intensity,
        story_allow_overlap=args.story_allow_overlap,
    )

    await generate_dataset(cfg)


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
