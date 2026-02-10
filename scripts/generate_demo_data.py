"""Generate realistic end-to-end demo data via the running Llamora server."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
import tomllib

import httpx
import orjson
import typer
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

from llamora.settings import settings
from demo_data_utils import (
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_str,
    iter_days,
    log_block,
    log_header,
    log_item,
    log_wrapped,
    parse_date,
    require_value,
    strip_outer_quotes,
)


logger = logging.getLogger(__name__)

HTTP_RETRIES_DEFAULT = 3
HTTP_RETRY_BASE_DELAY = 0.5


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


DEFAULTS: dict[str, Any] = {
    "base_url": "http://127.0.0.1:5000",
    "min_entries": 0,
    "max_entries": 3,
    "open_day_rate": 0.6,
    "open_only_rate": 0.15,
    "response_rate": 0.6,
    "max_tags": 4,
    "timezone": "UTC",
    "seed": 1337,
    "persona": (
        "A calm, reflective writer who notices small shifts in mood, light, and place. "
        "Often references nature, memory, and presence."
    ),
    "llm_max_tokens": 280,
    "min_entry_chars": 260,
    "max_entry_chars": 1200,
    "entry_retries": 2,
    "markdown_rate": 0.3,
    "entry_context_size": 4,
    "entry_context_chars": 2048,
    "entry_temperature": None,
    "empty_day_rate": 0.1,
    "multi_response_rate": 0.2,
    "max_responses_per_entry": 2,
    "story_events": 6,
    "story_followup_rate": 0.4,
    "story_intensity": 0.6,
    "story_allow_overlap": False,
}


def _load_demo_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "demo" in data and isinstance(data["demo"], dict):
        return dict(data["demo"])
    return dict(data) if isinstance(data, dict) else {}


def _build_demo_config(
    raw: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> DemoConfig:
    merged: dict[str, Any] = {**DEFAULTS, **raw}
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value

    start_date = parse_date(require_value(merged.get("start_date"), "start_date"))
    end_date = parse_date(require_value(merged.get("end_date"), "end_date"))

    return DemoConfig(
        base_url=coerce_str(merged.get("base_url"), DEFAULTS["base_url"]) or DEFAULTS["base_url"],
        username=require_value(merged.get("username"), "username"),
        password=require_value(merged.get("password"), "password"),
        start_date=start_date,
        end_date=end_date,
        min_entries=coerce_int(merged.get("min_entries"), DEFAULTS["min_entries"]),
        max_entries=coerce_int(merged.get("max_entries"), DEFAULTS["max_entries"]),
        open_day_rate=coerce_float(merged.get("open_day_rate"), DEFAULTS["open_day_rate"]),
        open_only_rate=coerce_float(merged.get("open_only_rate"), DEFAULTS["open_only_rate"]),
        response_rate=coerce_float(merged.get("response_rate"), DEFAULTS["response_rate"]),
        max_tags=coerce_int(merged.get("max_tags"), DEFAULTS["max_tags"]),
        tz=coerce_str(merged.get("timezone"), DEFAULTS["timezone"]) or DEFAULTS["timezone"],
        seed=coerce_int(merged.get("seed"), DEFAULTS["seed"]),
        persona_hint=coerce_str(merged.get("persona"), DEFAULTS["persona"]) or DEFAULTS["persona"],
        llm_max_tokens=coerce_int(merged.get("llm_max_tokens"), DEFAULTS["llm_max_tokens"]),
        markdown_rate=coerce_float(merged.get("markdown_rate"), DEFAULTS["markdown_rate"]),
        multi_response_rate=coerce_float(
            merged.get("multi_response_rate"), DEFAULTS["multi_response_rate"]
        ),
        max_responses_per_entry=coerce_int(
            merged.get("max_responses_per_entry"), DEFAULTS["max_responses_per_entry"]
        ),
        min_entry_chars=coerce_int(merged.get("min_entry_chars"), DEFAULTS["min_entry_chars"]),
        max_entry_chars=coerce_int(merged.get("max_entry_chars"), DEFAULTS["max_entry_chars"]),
        entry_retries=coerce_int(merged.get("entry_retries"), DEFAULTS["entry_retries"]),
        entry_context_size=coerce_int(
            merged.get("entry_context_size"), DEFAULTS["entry_context_size"]
        ),
        entry_context_chars=coerce_int(
            merged.get("entry_context_chars"), DEFAULTS["entry_context_chars"]
        ),
        entry_temperature=(
            None
            if merged.get("entry_temperature") is None
            else coerce_float(merged.get("entry_temperature"), 0.0)
        ),
        empty_day_rate=coerce_float(merged.get("empty_day_rate"), DEFAULTS["empty_day_rate"]),
        story_events=coerce_int(merged.get("story_events"), DEFAULTS["story_events"]),
        story_followup_rate=coerce_float(
            merged.get("story_followup_rate"), DEFAULTS["story_followup_rate"]
        ),
        story_intensity=coerce_float(
            merged.get("story_intensity"), DEFAULTS["story_intensity"]
        ),
        story_allow_overlap=coerce_bool(
            merged.get("story_allow_overlap"), DEFAULTS["story_allow_overlap"]
        ),
    )


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


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = HTTP_RETRIES_DEFAULT,
    base_delay: float = HTTP_RETRY_BASE_DELAY,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            last_exc = exc
            if attempt >= retries:
                break
            logger.warning(
                "Request error (%s) %s %s; retrying %s/%s",
                exc.__class__.__name__,
                method,
                url,
                attempt,
                retries,
            )
            await asyncio.sleep(base_delay * attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("Request failed without exception")


async def _get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    return await _request_with_retry(client, "GET", url, **kwargs)


async def _post(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    return await _request_with_retry(client, "POST", url, **kwargs)


@dataclass(slots=True)
class NarrativeEvent:
    date: date
    title: str
    summary: str
    details: list[str]
    tone: str
    emoji: str
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
    all_days = list(iter_days(config.start_date, config.end_date))
    if not all_days:
        return {}
    count = min(config.story_events, len(all_days))
    event_days = sorted(rng.sample(all_days, count))
    date_lines = "\n".join(f"- {d.isoformat()}" for d in event_days)
    prompt = (
        "Create a realistic life timeline for one person writing a diary. "
        "Return strict JSON array only, no extra text. Each item must include: "
        "date (YYYY-MM-DD), title, summary (1-2 sentences describing a concrete event), "
        "details (1-3 short factual fragments), emotional_tone, emoji (single emoji, e.g. 😃), "
        "followup_days (optional list of integers like 1,2,3). "
        "Events must be specific things that happened (not categories). "
        "Keep events plausible and varied. "
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
                        "summary": {"type": "string"},
                        "details": {"type": "array", "items": {"type": "string"}},
                        "emotional_tone": {"type": "string"},
                        "emoji": {"type": "string"},
                        "followup_days": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": [
                        "date",
                        "title",
                        "summary",
                        "details",
                        "emotional_tone",
                        "emoji",
                    ],
                    "additionalProperties": False,
                },
            },
        },
    }
    ctx_size = (
        settings.get("LLM.upstream.args.ctx_size")
        or settings.get("LLM.upstream.args.n_ctx")
    )
    try:
        max_tokens = int(ctx_size) if ctx_size is not None else None
    except (TypeError, ValueError):
        max_tokens = None
    if max_tokens is None or max_tokens <= 0:
        max_tokens = max(1200, int(config.llm_max_tokens) * 3)

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
        max_tokens=max_tokens,
        **params,
    )
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    raw = (choice.message.content or "").strip()
    try:
        import orjson

        data = orjson.loads(raw)
    except Exception:
        logger.warning(
            "Failed to parse timeline JSON; skipping narrative scaffold (finish_reason=%s, chars=%s)",
            finish_reason,
            len(raw),
        )
        if finish_reason == "length":
            logger.info(
                "Timeline generation truncated. Try raising --llm-max-tokens or lowering --story-events."
            )
        log_block("Timeline raw", raw)
        return {}
    if not isinstance(data, list):
        logger.warning("Timeline JSON is not a list; skipping narrative scaffold")
        return {}

    events_by_date: dict[date, list[NarrativeEvent]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        event_date = _parse_event_date(str(item.get("date") or ""))
        if event_date is None:
            continue
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        tone = str(item.get("emotional_tone") or "").strip()
        emoji = str(item.get("emoji") or "").strip()
        details_raw = item.get("details") or []
        details: list[str] = []
        if isinstance(details_raw, list):
            for detail in details_raw:
                detail_text = str(detail or "").strip()
                if detail_text:
                    details.append(detail_text)
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
        if not title and summary:
            title = summary[:40]
        if not summary and details:
            summary = details[0]
        if not emoji:
            emoji = "✨"
        event = NarrativeEvent(
            date=event_date,
            title=title,
            summary=summary,
            details=details,
            tone=tone,
            emoji=emoji,
            followup_days=followups,
        )
        events_by_date.setdefault(event_date, []).append(event)
        for offset in followups:
            follow_date = event_date + timedelta(days=offset)
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
        text = strip_outer_quotes(text)

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
    resp = await _get(client, "/register")
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
    resp = await _post(client, "/register", data=data)
    if resp.status_code >= 400:
        logger.warning("Register failed with status %s", resp.status_code)
        return False

    if "Recovery Code" in resp.text:
        log_item("registration: recovery screen received")
        return True

    error = _extract_auth_error(resp.text)
    if error:
        logger.warning("Register failed: %s", error)
        return False
    logger.warning("Register response did not include recovery screen; treating as failure")
    return False


async def _login_user(client: httpx.AsyncClient, config: DemoConfig) -> None:
    logger.debug("Logging in as %s", config.username)
    resp = await _get(client, "/login")
    resp.raise_for_status()
    csrf = _select_csrf_from_html(resp.text)
    if not csrf:
        raise RuntimeError("Failed to locate CSRF token on /login")

    data = {
        "username": config.username,
        "password": config.password,
        "csrf_token": csrf,
    }
    resp = await _post(client, "/login", data=data)
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
        resp = await _get(client, path)
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
    resp = await _get(client, f"/e/{day.isoformat()}", headers=headers)
    resp.raise_for_status()


async def _open_day_opening(client: httpx.AsyncClient, day: date, headers: dict[str, str]) -> None:
    logger.debug("Opening day opening stream %s", day)
    for attempt in range(1, HTTP_RETRIES_DEFAULT + 1):
        try:
            async with client.stream(
                "GET",
                f"/e/opening/{day.isoformat()}",
                headers=headers,
            ) as stream:
                await _consume_sse(stream)
            break
        except httpx.RequestError as exc:
            if attempt >= HTTP_RETRIES_DEFAULT:
                logger.warning(
                    "Day opening stream failed (%s) after %s attempts",
                    exc.__class__.__name__,
                    HTTP_RETRIES_DEFAULT,
                )
                break
            logger.warning(
                "Day opening stream error (%s); retrying %s/%s",
                exc.__class__.__name__,
                attempt,
                HTTP_RETRIES_DEFAULT,
            )
            await asyncio.sleep(HTTP_RETRY_BASE_DELAY * attempt)


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
    resp = await _post(client, f"/e/{day.isoformat()}/entry", data=data, headers=headers)
    if _is_login_page(resp.text, str(resp.url)):
        logger.warning("Session expired while posting entry; re-authenticating")
        csrf = await _login_and_refresh_csrf(client, config)
        headers["X-CSRFToken"] = csrf
        resp = await _post(client, f"/e/{day.isoformat()}/entry", data=data, headers=headers)
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
    try:
        resp = await _post(
            client,
            f"/e/{day.isoformat()}/response/{entry_id}",
            data=data,
            headers=headers,
        )
        resp.raise_for_status()
    except httpx.RequestError as exc:
        logger.warning(
            "Response trigger failed (%s) for %s",
            exc.__class__.__name__,
            entry_id,
        )
        return

    response_text = ""
    for attempt in range(1, HTTP_RETRIES_DEFAULT + 1):
        try:
            async with client.stream(
                "GET",
                f"/e/{day.isoformat()}/response/stream/{entry_id}",
                headers=headers,
            ) as stream:
                response_text = await _consume_sse(stream)
            break
        except httpx.RequestError as exc:
            if attempt >= HTTP_RETRIES_DEFAULT:
                logger.warning(
                    "Response stream failed (%s) after %s attempts",
                    exc.__class__.__name__,
                    HTTP_RETRIES_DEFAULT,
                )
                response_text = ""
                break
            logger.warning(
                "Response stream error (%s); retrying %s/%s",
                exc.__class__.__name__,
                attempt,
                HTTP_RETRIES_DEFAULT,
            )
            await asyncio.sleep(HTTP_RETRY_BASE_DELAY * attempt)
    if response_text:
        response_text = strip_outer_quotes(response_text)
        log_wrapped(
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
    try:
        resp = await _get(client, f"/t/suggestions/{entry_id}", headers=headers)
        resp.raise_for_status()
    except httpx.RequestError as exc:
        logger.warning(
            "Tag suggestions failed (%s) for %s",
            exc.__class__.__name__,
            entry_id,
        )
        return
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
        try:
            resp = await _post(client, f"/t/{entry_id}", data=data, headers=headers)
            resp.raise_for_status()
        except httpx.RequestError as exc:
            logger.warning(
                "Tag apply failed (%s) for %s", exc.__class__.__name__, entry_id
            )
            return


async def generate_dataset(config: DemoConfig) -> None:
    random.seed(config.seed)
    llm = _build_llm_client()
    response_kinds = _load_response_kinds()
    log_header(f"User: {config.username}")
    log_item(f"Range: {config.start_date} to {config.end_date} ({config.tz})")
    log_item(f"Persona: {config.persona_hint}")
    logger.debug("Response kinds: %s", ", ".join(response_kinds))
    recent_entries: deque[str] = deque(maxlen=max(0, config.entry_context_size))
    narrative_events = await _generate_narrative_timeline(llm, config)
    if narrative_events:
        logger.info("-" * 72)
        log_header("Narrative scaffold")
        for day in sorted(narrative_events.keys()):
            for event in narrative_events[day]:
                when = day.isoformat()
                title = event.title or event.summary or "event"
                detail = event.details[0] if event.details else event.summary
                log_item(f"{when}: {event.emoji} {title}")
                if detail:
                    logger.info("     %s", detail)
        logger.info("-" * 72)

    async with httpx.AsyncClient(
        base_url=config.base_url,
        follow_redirects=True,
        timeout=httpx.Timeout(60.0),
    ) as client:
        registered = await _register_user(client, config)
        if registered:
            log_item("auth: logging in")
        csrf = await _login_and_refresh_csrf(client, config)
        base_headers = {
            "X-CSRFToken": csrf,
            "X-Timezone": config.tz,
        }

        total_entries = 0
        opened_any_day = False
        for day in iter_days(config.start_date, config.end_date):
            logger.info("-" * 72)
            log_header(f"Day {day.isoformat()}")
            headers = {
                **base_headers,
                "X-Client-Today": day.isoformat(),
            }
            if random.random() < config.empty_day_rate:
                log_item("empty day")
                continue
            event_note = None
            followup_note = None
            events_today = narrative_events.get(day) or []
            if events_today:
                event = events_today[0]
                if event.date == day:
                    detail = event.details[0] if event.details else event.summary
                    if random.random() < config.story_intensity:
                        event_note = (
                            f"{event.emoji} {event.summary or event.title}. "
                            f"{detail}. tone: {event.tone}."
                        )
                    else:
                        event_note = (
                            f"{event.emoji} {event.summary or event.title} ({event.tone})."
                        )
                    log_item(f"event: {event.emoji} {event.title or event.summary}")
                    if event_note:
                        log_wrapped("     note: ", event_note)
                else:
                    followup_note = (
                        f"{event.emoji} {event.summary or event.title} ({event.tone})"
                    )
                    log_item(f"followup: {followup_note}")
            open_day = True
            open_only = random.random() < config.open_only_rate

            if open_day:
                log_item("opening: yes")
                try:
                    await _open_day(client, day, headers)
                    await _open_day_opening(client, day, headers)
                except httpx.RequestError as exc:
                    logger.warning(
                        "Day open failed (%s); skipping day %s",
                        exc.__class__.__name__,
                        day.isoformat(),
                    )
                    continue
                opened_any_day = True

            if open_only:
                log_item("entries: 0 (opening only)")
                continue

            entries_today = random.randint(config.min_entries, config.max_entries)
            if entries_today == 0:
                log_item("entries: 0")
                continue

            times = _choose_entry_times(entries_today, day)
            log_item(f"entries: {entries_today}")
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
                    log_wrapped("     ", text.strip())
                    recent_entries.append(text)
                try:
                    entry_id = await _create_entry(
                        client, day, text, entry_time, headers, config
                    )
                except httpx.RequestError as exc:
                    logger.warning(
                        "Entry post failed (%s) for %s; skipping responses/tags",
                        exc.__class__.__name__,
                        day.isoformat(),
                    )
                    continue
                except RuntimeError as exc:
                    logger.warning("Entry post failed for %s: %s", day.isoformat(), exc)
                    continue
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


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        if verbose
        else "%(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


app = typer.Typer(help="Generate realistic demo data via the running Llamora server.")


@app.command("run")
def run_cmd(
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to a TOML config file (e.g. config/demo_data.example.toml).",
        exists=True,
        dir_okay=False,
        file_okay=True,
        readable=True,
    ),
    base_url: str | None = typer.Option(None, help="Base URL for the running server."),
    username: str | None = typer.Option(None, help="Username for the demo account."),
    password: str | None = typer.Option(None, help="Password for the demo account."),
    start_date: str | None = typer.Option(None, help="Start date (YYYY-MM-DD)."),
    end_date: str | None = typer.Option(None, help="End date (YYYY-MM-DD)."),
    min_entries: int | None = typer.Option(None, help="Minimum entries per day."),
    max_entries: int | None = typer.Option(None, help="Maximum entries per day."),
    open_only_rate: float | None = typer.Option(None, help="Chance of opening only."),
    response_rate: float | None = typer.Option(None, help="Chance of responses."),
    max_tags: int | None = typer.Option(None, help="Max tags per entry."),
    timezone: str | None = typer.Option(None, help="IANA timezone (e.g. Europe/Amsterdam)."),
    seed: int | None = typer.Option(None, help="Random seed."),
    persona: str | None = typer.Option(None, help="Persona hint for entries."),
    llm_max_tokens: int | None = typer.Option(None, help="Max tokens per entry."),
    entry_temperature: float | None = typer.Option(None, help="Entry temperature override."),
    markdown_rate: float | None = typer.Option(None, help="Chance of markdown in entries."),
    entry_context_size: int | None = typer.Option(None, help="Recent entries to include."),
    entry_context_chars: int | None = typer.Option(None, help="Chars per context snippet."),
    min_entry_chars: int | None = typer.Option(None, help="Minimum entry length."),
    max_entry_chars: int | None = typer.Option(None, help="Maximum entry length."),
    entry_retries: int | None = typer.Option(None, help="Retries for short entries."),
    empty_day_rate: float | None = typer.Option(None, help="Chance of empty day."),
    story_events: int | None = typer.Option(None, help="Narrative event count."),
    story_followup_rate: float | None = typer.Option(None, help="Chance of follow-up days."),
    story_intensity: float | None = typer.Option(None, help="Event injection strength."),
    story_allow_overlap: bool | None = typer.Option(None, help="Allow event overlap."),
    multi_response_rate: float | None = typer.Option(None, help="Chance of multi responses."),
    max_responses_per_entry: int | None = typer.Option(None, help="Max responses per entry."),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose logging."),
) -> None:
    _setup_logging(verbose)
    raw_cfg = _load_demo_config(config)
    overrides = {
        "base_url": base_url,
        "username": username,
        "password": password,
        "start_date": start_date,
        "end_date": end_date,
        "min_entries": min_entries,
        "max_entries": max_entries,
        "open_only_rate": open_only_rate,
        "response_rate": response_rate,
        "max_tags": max_tags,
        "timezone": timezone,
        "seed": seed,
        "persona": persona,
        "llm_max_tokens": llm_max_tokens,
        "entry_temperature": entry_temperature,
        "markdown_rate": markdown_rate,
        "entry_context_size": entry_context_size,
        "entry_context_chars": entry_context_chars,
        "min_entry_chars": min_entry_chars,
        "max_entry_chars": max_entry_chars,
        "entry_retries": entry_retries,
        "empty_day_rate": empty_day_rate,
        "story_events": story_events,
        "story_followup_rate": story_followup_rate,
        "story_intensity": story_intensity,
        "story_allow_overlap": story_allow_overlap,
        "multi_response_rate": multi_response_rate,
        "max_responses_per_entry": max_responses_per_entry,
    }
    try:
        cfg = _build_demo_config(raw_cfg, overrides)
    except ValueError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=1) from exc

    asyncio.run(generate_dataset(cfg))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
