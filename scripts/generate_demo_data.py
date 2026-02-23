"""Generate realistic end-to-end demo data via the running Llamora server."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import textwrap
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
from rich.box import ROUNDED
from rich.table import Table

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
    log_rich,
    log_rule,
    log_wrapped,
    parse_date,
    require_value,
    strip_outer_quotes,
)


logger = logging.getLogger(__name__)

HTTP_RETRIES_DEFAULT = 3
HTTP_RETRY_BASE_DELAY = 0.5
EMOJI_TAG_RE = re.compile(
    "["
    "\U0001f1e6-\U0001f1ff"  # flags
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001faff"
    "\u2600-\u26ff"
    "\u2700-\u27bf"
    "]"
)


@dataclass(slots=True)
class DemoConfig:
    base_url: str
    username: str
    password: str
    start_date: date
    end_date: date
    min_entries: int
    max_entries: int
    day_open_only_rate: float
    day_empty_rate: float
    response_rate: float
    min_tags: int
    max_tags: int
    tz: str
    seed: int
    persona_hint: str
    llm_max_tokens: int
    small_entry_rate: float
    medium_entry_rate: float
    large_entry_rate: float
    multi_response_rate: float
    max_responses_per_entry: int
    min_entry_chars: int
    max_entry_chars: int
    entry_retries: int
    entry_context_size: int
    entry_context_chars: int
    entry_temperature: float | None
    story_events: int
    story_followup_rate: float
    story_intensity: float
    story_allow_overlap: bool


DEFAULTS: dict[str, Any] = {
    "base_url": "http://127.0.0.1:5000",
    "min_entries": 0,
    "max_entries": 3,
    "day_open_only_rate": 0.15,
    "day_empty_rate": 0.1,
    "response_rate": 0.6,
    "min_tags": 1,
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
    "small_entry_rate": 0.45,
    "medium_entry_rate": 0.35,
    "large_entry_rate": 0.20,
    "entry_context_size": 4,
    "entry_context_chars": 2048,
    "entry_temperature": None,
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

    day_open_only_rate = coerce_float(
        merged.get("day_open_only_rate", merged.get("open_only_rate")),
        DEFAULTS["day_open_only_rate"],
    )
    day_empty_rate = coerce_float(
        merged.get("day_empty_rate", merged.get("empty_day_rate")),
        DEFAULTS["day_empty_rate"],
    )

    min_tags = coerce_int(merged.get("min_tags"), DEFAULTS["min_tags"])
    max_tags = coerce_int(merged.get("max_tags"), DEFAULTS["max_tags"])
    if min_tags < 0:
        raise ValueError("min_tags must be >= 0")
    if max_tags < 0:
        raise ValueError("max_tags must be >= 0")
    if min_tags > max_tags:
        raise ValueError("min_tags cannot be greater than max_tags")

    small_entry_rate = coerce_float(
        merged.get("small_entry_rate"), DEFAULTS["small_entry_rate"]
    )
    medium_entry_rate = coerce_float(
        merged.get("medium_entry_rate"), DEFAULTS["medium_entry_rate"]
    )
    large_entry_rate = coerce_float(
        merged.get("large_entry_rate"), DEFAULTS["large_entry_rate"]
    )
    if small_entry_rate < 0 or medium_entry_rate < 0 or large_entry_rate < 0:
        raise ValueError("Entry size rates must be >= 0")
    size_rate_sum = small_entry_rate + medium_entry_rate + large_entry_rate
    if abs(size_rate_sum - 1.0) > 1e-6:
        raise ValueError(
            "small_entry_rate + medium_entry_rate + large_entry_rate must equal 1.0"
        )

    return DemoConfig(
        base_url=coerce_str(merged.get("base_url"), DEFAULTS["base_url"])
        or DEFAULTS["base_url"],
        username=require_value(merged.get("username"), "username"),
        password=require_value(merged.get("password"), "password"),
        start_date=start_date,
        end_date=end_date,
        min_entries=coerce_int(merged.get("min_entries"), DEFAULTS["min_entries"]),
        max_entries=coerce_int(merged.get("max_entries"), DEFAULTS["max_entries"]),
        day_open_only_rate=day_open_only_rate,
        day_empty_rate=day_empty_rate,
        response_rate=coerce_float(
            merged.get("response_rate"), DEFAULTS["response_rate"]
        ),
        min_tags=min_tags,
        max_tags=max_tags,
        tz=coerce_str(merged.get("timezone"), DEFAULTS["timezone"])
        or DEFAULTS["timezone"],
        seed=coerce_int(merged.get("seed"), DEFAULTS["seed"]),
        persona_hint=coerce_str(merged.get("persona"), DEFAULTS["persona"])
        or DEFAULTS["persona"],
        llm_max_tokens=coerce_int(
            merged.get("llm_max_tokens"), DEFAULTS["llm_max_tokens"]
        ),
        small_entry_rate=small_entry_rate,
        medium_entry_rate=medium_entry_rate,
        large_entry_rate=large_entry_rate,
        multi_response_rate=coerce_float(
            merged.get("multi_response_rate"), DEFAULTS["multi_response_rate"]
        ),
        max_responses_per_entry=coerce_int(
            merged.get("max_responses_per_entry"), DEFAULTS["max_responses_per_entry"]
        ),
        min_entry_chars=coerce_int(
            merged.get("min_entry_chars"), DEFAULTS["min_entry_chars"]
        ),
        max_entry_chars=coerce_int(
            merged.get("max_entry_chars"), DEFAULTS["max_entry_chars"]
        ),
        entry_retries=coerce_int(
            merged.get("entry_retries"), DEFAULTS["entry_retries"]
        ),
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
    followup_note: str
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
    prompt = textwrap.dedent(
        """
        Create a realistic life timeline for one person writing a diary.
        Use the persona details to shape the kinds of events, settings, and details.
        Return strict JSON array only, no extra text. Each item must include:
        - date (YYYY-MM-DD)
        - title
        - summary (3-4 sentences describing a concrete event)
        - emoji (single emoji character; never empty, never null; do not use ✨)
        - followup_note (1 sentence about the AFTERMATH/echo of the event; do not repeat the event details;
          focus on lingering feelings, small consequences, or what changes afterward)
        - followup_days (list of integers like 1,2,3; include 1-3 for most events)

        Include specific details (place, people, objects, actions) inside the summary.
        Avoid generic phrasing; make each event distinct, grounded, and aligned with the persona.
        Events must be specific things that happened (not categories).
        Vary the kinds of events (work, home, social, health, learning, travel, chores, small wins).
        Avoid repeating the same verbs or settings. Include small quirks or concrete sensory details.
        If any provided dates fall on notable days or holidays (e.g. New Year's), make those events feel weightier.
        Keep events plausible and varied.
        Use the provided dates exactly and keep the order.
        """
    ).strip()
    user_message = textwrap.dedent(
        f"""
        Start date: {config.start_date.isoformat()}
        End date: {config.end_date.isoformat()}
        Count: {count}
        Followup chance: {config.story_followup_rate}
        Persona: {config.persona_hint}

        Make the events feel like they belong to this persona (work, interests, habits, voice).
        Event dates (use exactly, keep order):
        {date_lines}

        {prompt}
        """
    ).strip()
    model = settings.get("LLM.chat.model") or "local"
    params = dict(settings.get("LLM.chat.parameters") or {})
    params["temperature"] = min(0.7, max(0.1, float(params.get("temperature", 0.4))))
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
                        "emoji": {"type": "string"},
                        "followup_note": {"type": "string"},
                        "followup_days": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": [
                        "date",
                        "title",
                        "summary",
                        "followup_note",
                        "emoji",
                    ],
                    "additionalProperties": False,
                },
            },
        },
    }
    ctx_size = settings.get("LLM.upstream.ctx_size")
    try:
        max_tokens = int(ctx_size) if ctx_size is not None else None
    except (TypeError, ValueError):
        max_tokens = None
    if max_tokens is None or max_tokens <= 0:
        max_tokens = max(1200, int(config.llm_max_tokens) * 3)

    data: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    raw = ""
    for attempt in range(2):
        response = await llm.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You design personal timelines for a specific persona. "
                        "Return only JSON, no code, no markdown. "
                        "Every item must include a non-empty emoji and a followup_note."
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

            parsed = orjson.loads(raw)
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
        if not isinstance(parsed, list):
            logger.warning("Timeline JSON is not a list; skipping narrative scaffold")
            return {}
        data = parsed
        missing = [
            item
            for item in data
            if isinstance(item, dict) and not str(item.get("emoji") or "").strip()
        ]
        if not missing:
            break
        if attempt == 0:
            logger.warning("Timeline missing emoji; retrying with stricter prompt")
            user_message = textwrap.dedent(
                f"""
                {user_message}

                IMPORTANT: Each item MUST include a non-empty emoji character
                AND a followup_note. Do not use null. If unsure, pick a simple emoji like
                🙂, 😌, 🤔, 🎉, 📌.
                """
            ).strip()
            continue
        logger.warning("Timeline still missing emoji after retry; keeping blanks")

    if data is None:
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
        followup_note = str(item.get("followup_note") or "").strip()
        raw_emoji = item.get("emoji")
        emoji = str(raw_emoji or "").strip()
        followups_raw = item.get("followup_days") or []
        followups: list[int] = []
        if isinstance(followups_raw, list):
            for val in followups_raw:
                try:
                    followups.append(int(val))
                except Exception:
                    continue
        followup_set = set(followups)
        if not followup_set and random.random() < config.story_followup_rate:
            followup_set.add(random.choice([1, 2, 3]))
        if followup_set and random.random() < config.story_followup_rate:
            extra_count = random.choice([1, 2])
            for _ in range(extra_count):
                followup_set.add(random.choice([1, 2, 3, 5, 7]))
        followups = sorted(followup_set)
        if not title and summary:
            title = summary[:40]
        if not summary and title:
            summary = title
        if not emoji:
            logger.warning(
                "timeline warning: missing emoji for %s (raw=%r)",
                event_date.isoformat(),
                raw_emoji,
            )
        if not followup_note:
            followup_note = summary
        event = NarrativeEvent(
            date=event_date,
            title=title,
            summary=summary,
            followup_note=followup_note,
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
            primary = [event for event in events if event.date == d]
            if primary:
                events_by_date[d] = [primary[0]]
            else:
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


_WEEKDAY_SLOTS = [(7, 8), (12, 13), (18, 23)]
_WEEKDAY_WEIGHTS = [0.15, 0.15, 0.70]

_WEEKEND_SLOTS = [(8, 12), (13, 17), (19, 23)]
_WEEKEND_WEIGHTS = [0.40, 0.30, 0.30]

_MIN_GAP_MINUTES = 20


def _choose_entry_times(count: int, day: date) -> list[datetime]:
    if count <= 0:
        return []

    is_weekend = day.weekday() >= 5
    slots = _WEEKEND_SLOTS if is_weekend else _WEEKDAY_SLOTS
    weights = _WEEKEND_WEIGHTS if is_weekend else _WEEKDAY_WEIGHTS

    times: list[datetime] = []
    for _ in range(count):
        window = random.choices(slots, weights=weights, k=1)[0]
        hour = random.randint(window[0], window[1])
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        dt = datetime.combine(day, time(hour, minute, second), tzinfo=timezone.utc)
        times.append(dt)
    times.sort()

    # Enforce a minimum gap between consecutive entries.
    gap = timedelta(minutes=_MIN_GAP_MINUTES)
    for i in range(1, len(times)):
        if times[i] - times[i - 1] < gap:
            times[i] = times[i - 1] + gap + timedelta(seconds=random.randint(0, 120))
    return times


def _humanize_days_ago(delta_days: int) -> str:
    if delta_days <= 0:
        return "today"
    if delta_days == 1:
        return "yesterday"
    if delta_days == 7:
        return "a week ago"
    if delta_days % 7 == 0 and delta_days >= 14:
        weeks = delta_days // 7
        return f"{weeks} weeks ago"
    return f"{delta_days} days ago"


def _is_emoji_tag(value: str) -> bool:
    return bool(EMOJI_TAG_RE.search(value or ""))


def _tokenize_text(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9-]*", (value or "").lower())
    return {token for token in tokens if token}


def _score_tag_candidate(
    tag: str,
    *,
    entry_text_lower: str,
    entry_tokens: set[str],
    recent_penalty: Mapping[str, int],
) -> float:
    if _is_emoji_tag(tag):
        # Keep emoji tags possible, but slightly de-prioritize by default.
        return 0.15 + random.random() * 0.1

    tag_lower = tag.lower()
    parts = [part for part in re.split(r"[-_\s]+", tag_lower) if part]
    full_match = 1.2 if tag_lower in entry_text_lower else 0.0
    part_hits = 0.0
    for part in parts:
        if part in entry_tokens or part in entry_text_lower:
            part_hits += 0.8
    penalty = float(recent_penalty.get(tag_lower, 0)) * 0.7
    noise = random.random() * 0.25
    return full_match + part_hits + noise - penalty


def _parse_tag_selection_payload(raw: str) -> list[str] | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        parsed = orjson.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [str(item or "").strip() for item in parsed if str(item or "").strip()]

    # Fallback for lax model output like: [🚫, ai-models, problem-solving]
    if not (text.startswith("[") and text.endswith("]")):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return []
    raw_tokens = re.findall(r'"(?:\\.|[^"])*"|\'(?:\\.|[^\'])*\'|[^,\n]+', inner)
    tokens: list[str] = []
    for token in raw_tokens:
        value = token.strip().strip("`")
        if not value:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        value = value.strip()
        if value:
            tokens.append(value)
    return tokens


async def _generate_entry_text(
    llm: AsyncOpenAI,
    config: DemoConfig,
    entry_date: date,
    entry_time: datetime,
    index: int,
    recent_entries: list[str],
    event_note: str | None,
    followup_note: str | None,
) -> str:
    persona = config.persona_hint.strip()
    time_label = entry_time.strftime("%H:%M")

    # Soft length variation: quick snippets, normal notes, and very large markdown entries.
    length_mode = random.choices(
        ["small", "medium", "large"],
        weights=[
            config.small_entry_rate,
            config.medium_entry_rate,
            config.large_entry_rate,
        ],
        k=1,
    )[0]
    length_hint = ""
    target_min_chars = config.min_entry_chars
    target_max_chars = config.max_entry_chars
    max_tokens = config.llm_max_tokens
    if length_mode == "medium":
        target_min_chars = max(220, min(config.min_entry_chars, 420))
        target_max_chars = max(
            target_min_chars + 200, min(config.max_entry_chars, 1400)
        )
        length_hint = (
            "Write a normal journal note with a little depth.\n"
            f"Target length: about {target_min_chars}-{target_max_chars} characters.\n"
        )
    elif length_mode == "large":
        target_min_chars = max(config.min_entry_chars, 1600)
        target_max_chars = max(config.max_entry_chars, 3800)
        max_tokens = max(config.llm_max_tokens, 1200)
        length_hint = textwrap.dedent(
            f"""
            Write a very large markdown entry.
            Target length: about {target_min_chars}-{target_max_chars} characters.
            Structure it with:
            - at least one markdown header (`##` or `###`)
            - a bullet or numbered list, table, quote, etc.
            """
        ).strip()
    else:
        # "small": tweet-like entry.
        target_min_chars = 80
        target_max_chars = 280
        max_tokens = min(config.llm_max_tokens, 220)
        length_hint = (
            "Write a tweet-length note only. Keep it concise and specific.\n"
            f"Hard cap: {target_max_chars} characters.\n"
        )

    user_message = textwrap.dedent(
        f"""
        You are using a journaling / diary app to write a personal note for yourself.
        Write the note directly, without restating the date, time, entry number, or persona.
        Use normal, everyday language.
        Do not add a title.
        Do not wrap the entry in quotation marks or start with a quote.
        It is fine to write about only one small thing, or nothing important at all.

        Context (do NOT include this information in the entry):
        - Date: {entry_date.isoformat()}
        - Time: {time_label}
        - Entry number today: {index + 1}
        - Persona: {persona}

        {length_hint}
        """
    ).strip()
    if event_note:
        user_message += textwrap.dedent(
            f"""

            Event note (do not quote): {event_note}
            """
        )
    if followup_note:
        user_message += textwrap.dedent(
            f"""

            Recent event context (lightly reflect on it): {followup_note}
            """
        )

    # Context dropout + thinning: not every entry gets continuity, and we only
    # include 1–2 recent entries when we do.
    continuity_message = None
    if recent_entries and random.random() < 0.7:  # 30% chance: no context
        max_entries = random.choice([1, 2])
        chosen = recent_entries[-max_entries:]
        snippet_chars = max(64, int(config.entry_context_chars))
        snippets = "\n".join(
            f"- {entry[:snippet_chars].strip()}" for entry in chosen if entry.strip()
        )
        continuity_message = textwrap.dedent(
            f"""
            Previous entries (reference only; do not copy wording or structure):
            {snippets}
            """
        ).strip()

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
            max_tokens=max_tokens,
            **params,
        )

        text = (response.choices[0].message.content or "").strip()
        if not text:
            continue
        text = strip_outer_quotes(text)

        if len(text) < target_min_chars:
            extra = (
                "You can expand slightly on the same situation.\n"
                if length_mode != "small"
                else ""
            )
            user_message = textwrap.dedent(
                f"""
                {user_message}

                Write a bit more (at least {target_min_chars} characters).
                {extra}Do not add metadata or repeat earlier concerns unless needed.
                """
            ).strip()
            continue

        if len(text) > target_max_chars:
            text = text[:target_max_chars].rsplit(" ", 1)[0].rstrip()

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
    logger.warning(
        "Register response did not include recovery screen; treating as failure"
    )
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
            logger.warning(
                "CSRF refresh failed for %s (status=%s)", path, resp.status_code
            )
            continue
        csrf = _select_body_csrf(resp.text) or _select_csrf_from_html(resp.text)
        if csrf:
            logger.debug("CSRF token loaded from %s", path)
            return csrf
        logger.debug("CSRF token not found on %s (url=%s)", path, str(resp.url))
    raise RuntimeError("Failed to locate CSRF token from known pages")


async def _open_day(
    client: httpx.AsyncClient, day: date, headers: dict[str, str]
) -> None:
    logger.debug("Opening day %s", day)
    resp = await _get(client, f"/e/{day.isoformat()}", headers=headers)
    resp.raise_for_status()


async def _open_day_opening(
    client: httpx.AsyncClient, day: date, headers: dict[str, str]
) -> None:
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
    resp = await _post(
        client, f"/e/{day.isoformat()}/entry", data=data, headers=headers
    )
    if _is_login_page(resp.text, str(resp.url)):
        logger.warning("Session expired while posting entry; re-authenticating")
        csrf = await _login_and_refresh_csrf(client, config)
        headers["X-CSRFToken"] = csrf
        resp = await _post(
            client, f"/e/{day.isoformat()}/entry", data=data, headers=headers
        )
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
) -> None:
    logger.debug("Triggering response for entry %s", entry_id)
    data = {
        "user_time": user_time.isoformat(),
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
        log_wrapped("  assistant: ", response_text.strip())


async def _select_tags_with_llm(
    llm: AsyncOpenAI,
    config: DemoConfig,
    entry_text: str,
    suggestions: list[str],
    min_tags: int,
    max_tags: int,
    recent_tag_penalty: Mapping[str, int] | None = None,
) -> list[str]:
    if not suggestions or max_tags <= 0:
        return []
    effective_max = min(max_tags, len(suggestions))
    if effective_max <= 0:
        return []
    effective_min = min(max(0, min_tags), effective_max)
    target_tag_count = random.randint(effective_min, effective_max)
    if target_tag_count <= 0:
        return []

    unique_suggestions: list[str] = []
    seen: set[str] = set()
    for suggestion in suggestions:
        value = str(suggestion or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique_suggestions.append(value)
    if not unique_suggestions:
        return []
    random.shuffle(unique_suggestions)

    effective_max = min(target_tag_count, len(unique_suggestions))
    effective_min = min(effective_min, effective_max)
    if effective_max <= 0:
        return []

    emoji_suggestions = [tag for tag in unique_suggestions if _is_emoji_tag(tag)]
    max_emoji_tags = 1
    entry_text_lower = (entry_text or "").lower()
    entry_tokens = _tokenize_text(entry_text)
    recent_penalty = {
        str(k).lower(): max(0, int(v))
        for k, v in (recent_tag_penalty or {}).items()
        if str(k).strip()
    }
    model = settings.get("LLM.chat.model") or "local"
    params = dict(settings.get("LLM.chat.parameters") or {})
    base_temp = float(params.get("temperature", 0.25))
    params["temperature"] = min(0.65, max(0.18, base_temp + random.uniform(0.0, 0.2)))
    params["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "tag_choice",
            "strict": True,
            "schema": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": effective_max,
                "maxItems": effective_max,
            },
        },
    }
    tag_lines = "\n".join(f"- {tag}" for tag in unique_suggestions)
    emoji_hint = (
        "Use at most one emoji tag in the final list." if emoji_suggestions else ""
    )
    recent_hint = ""
    if recent_penalty:
        recent_sorted = sorted(
            recent_penalty.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        recent_top = [tag for tag, _ in recent_sorted[:8]]
        if recent_top:
            recent_hint = (
                "Recently overused tags (avoid unless strongly relevant): "
                + ", ".join(recent_top)
            )
    user_message = textwrap.dedent(
        f"""
        Pick the most relevant tags for the entry from the suggestions list.
        Return a JSON array only (no markdown, no code fences).
        Choose exactly {effective_max} tags.
        Prefer specific tags tied to this exact entry, not generic persona-level tags.
        {emoji_hint}
        {recent_hint}

        Entry:
        {entry_text}

        Suggestions:
        {tag_lines}
        """
    ).strip()
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
    parsed = _parse_tag_selection_payload(raw)
    if parsed is None:
        logger.info("Tag selection raw (truncated): %s", raw[:1200])
        logger.warning("Failed to parse tag selection JSON; using heuristic fallback")
        parsed = []
    tags = parsed
    allowed = {tag.strip() for tag in unique_suggestions}
    selected: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if not value or value not in allowed or value in selected:
            continue
        selected.append(value)
        if len(selected) >= effective_max:
            break
    # Enforce the hard cap of one emoji tag.
    emoji_positions = [
        idx for idx, value in enumerate(selected) if _is_emoji_tag(value)
    ]
    if len(emoji_positions) > max_emoji_tags:
        non_emoji_pool = [
            tag
            for tag in unique_suggestions
            if not _is_emoji_tag(tag) and tag not in selected
        ]
        for idx in emoji_positions[max_emoji_tags:]:
            if non_emoji_pool:
                selected[idx] = non_emoji_pool.pop(0)
            else:
                selected[idx] = ""
        selected = [tag for tag in selected if tag]

    # Fill to requested count when model returns too few tags.
    if len(selected) < effective_max:
        fallback_pool = sorted(
            (tag for tag in unique_suggestions if tag not in selected),
            key=lambda tag: _score_tag_candidate(
                tag,
                entry_text_lower=entry_text_lower,
                entry_tokens=entry_tokens,
                recent_penalty=recent_penalty,
            ),
            reverse=True,
        )
        for tag in fallback_pool:
            if (
                _is_emoji_tag(tag)
                and sum(1 for current in selected if _is_emoji_tag(current))
                >= max_emoji_tags
            ):
                continue
            selected.append(tag)
            if len(selected) >= effective_max:
                break

    return selected


async def _apply_tags(
    llm: AsyncOpenAI,
    config: DemoConfig,
    client: httpx.AsyncClient,
    entry_id: str,
    entry_text: str,
    min_tags: int,
    max_tags: int,
    recent_tag_penalty: Mapping[str, int] | None,
    headers: dict[str, str],
) -> list[str]:
    try:
        resp = await _get(client, f"/t/entry/{entry_id}/suggestions", headers=headers)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "Tag suggestions failed (%s) for %s",
            exc.__class__.__name__,
            entry_id,
        )
        return []
    suggestions = _select_tag_suggestions(resp.text)
    if not suggestions:
        logger.debug("No tag suggestions for %s", entry_id)
        return []

    selected = await _select_tags_with_llm(
        llm,
        config,
        entry_text,
        suggestions,
        min_tags,
        max_tags,
        recent_tag_penalty=recent_tag_penalty,
    )
    if not selected:
        logger.info("    tags: (none)")
        return []
    logger.info("    tags: %s", ", ".join(selected))
    for tag in selected:
        logger.debug("Adding tag '%s' to %s", tag, entry_id)
        data = {"tag": tag}
        try:
            resp = await _post(
                client, f"/t/entry/{entry_id}", data=data, headers=headers
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "Tag apply failed (%s) for %s", exc.__class__.__name__, entry_id
            )
            return selected
    return selected


async def generate_dataset(config: DemoConfig) -> None:
    random.seed(config.seed)
    llm = _build_llm_client()
    log_header(f"User: {config.username}")
    log_item(f"Range: {config.start_date} to {config.end_date} ({config.tz})")
    log_item(f"Persona: {config.persona_hint}")
    log_item(f"Tags per entry: {config.min_tags} to {config.max_tags}")
    log_item(
        "Entry size rates: "
        f"small={config.small_entry_rate:.2f}, "
        f"medium={config.medium_entry_rate:.2f}, "
        f"large={config.large_entry_rate:.2f}"
    )
    recent_entries: deque[str] = deque(maxlen=max(0, config.entry_context_size))
    recent_tag_window: deque[list[str]] = deque()
    recent_tag_counts: dict[str, int] = {}
    recent_tag_window_limit = 30
    narrative_events = await _generate_narrative_timeline(llm, config)
    if narrative_events:
        log_rule("Narrative scaffold")
        primary_events: list[NarrativeEvent] = []
        for day in sorted(narrative_events.keys()):
            for event in narrative_events[day]:
                if event.date == day:
                    primary_events.append(event)

        table = Table(
            box=ROUNDED,
            show_header=True,
            show_lines=True,
            expand=True,
            padding=(0, 1),
        )
        table.add_column("date", no_wrap=True, style="dim")
        table.add_column("title", style="bold")
        table.add_column("summary", overflow="fold")
        table.add_column("followups", no_wrap=True, style="dim")
        for event in primary_events:
            followups = ",".join(f"+{d}d" for d in event.followup_days) or "-"
            title = f"{event.emoji} {event.title or event.summary or 'event'}".strip()
            table.add_row(
                event.date.isoformat(),
                title,
                event.summary,
                followups,
            )
        log_rich(table)
        log_rule("")

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
        total_tags = 0
        for day in iter_days(config.start_date, config.end_date):
            log_rule(f"Day {day.isoformat()}")
            headers = {
                **base_headers,
                "X-Client-Today": day.isoformat(),
            }
            event_note = None
            followup_note = None
            events_today = narrative_events.get(day) or []
            if events_today:
                event = events_today[0]
                if event.date == day:
                    if random.random() < config.story_intensity:
                        event_note = f"{event.emoji} {event.summary or event.title}."
                    else:
                        event_note = f"{event.emoji} {event.summary or event.title}."
                    log_item(f"event: {event.emoji} {event.title or event.summary}")
                    if event_note:
                        log_wrapped("     note: ", event_note)
                else:
                    delta_days = (day - event.date).days
                    rel = _humanize_days_ago(delta_days)
                    followup_note = (
                        f"{event.emoji} Follow-up from {rel}: "
                        f"{event.followup_note or event.summary or event.title}."
                    )
                    log_item(f"followup: {followup_note}")
            has_event_context = bool(events_today)
            if random.random() < config.day_empty_rate and not has_event_context:
                log_item("empty day")
                continue

            open_day = True
            open_only = random.random() < config.day_open_only_rate
            if has_event_context:
                open_only = False

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
            if open_only:
                log_item("entries: 0 (opening only)")
                continue

            entries_today = random.randint(config.min_entries, config.max_entries)
            if has_event_context and entries_today == 0:
                entries_today = 1
            if entries_today == 0:
                log_item("entries: 0")
                continue

            times = _choose_entry_times(entries_today, day)
            log_item(f"entries: {entries_today}")
            for idx, entry_time in enumerate(times):
                logger.debug(
                    "Generating entry %s/%s for %s", idx + 1, entries_today, day
                )
                text = await _generate_entry_text(
                    llm,
                    config,
                    day,
                    entry_time,
                    idx,
                    list(recent_entries),
                    event_note,
                    followup_note,
                )
                if text:
                    logger.info(
                        "  entry %s/%s @ %s",
                        idx + 1,
                        entries_today,
                        entry_time.strftime("%H:%M"),
                    )
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

                selected_tags = await _apply_tags(
                    llm,
                    config,
                    client,
                    entry_id,
                    text,
                    config.min_tags,
                    config.max_tags,
                    recent_tag_counts,
                    headers,
                )
                total_tags += len(selected_tags)
                if selected_tags:
                    recent_tag_window.append(list(selected_tags))
                    for raw_tag in selected_tags:
                        key = raw_tag.strip().lower()
                        if not key:
                            continue
                        recent_tag_counts[key] = recent_tag_counts.get(key, 0) + 1
                    while len(recent_tag_window) > recent_tag_window_limit:
                        removed = recent_tag_window.popleft()
                        for removed_tag in removed:
                            key = removed_tag.strip().lower()
                            if not key:
                                continue
                            count = recent_tag_counts.get(key, 0) - 1
                            if count <= 0:
                                recent_tag_counts.pop(key, None)
                            else:
                                recent_tag_counts[key] = count

                if random.random() < config.response_rate:
                    response_count = 1
                    if (
                        config.max_responses_per_entry > 1
                        and random.random() < config.multi_response_rate
                    ):
                        response_count = random.randint(
                            2, config.max_responses_per_entry
                        )
                    for _ in range(response_count):
                        await _trigger_response(
                            client,
                            day,
                            entry_id,
                            entry_time,
                            headers,
                        )

        avg_tags = (total_tags / total_entries) if total_entries else 0.0
        logger.info(
            "Done. Created %d entries, applied %d tags (avg %.2f per entry).",
            total_entries,
            total_tags,
            avg_tags,
        )


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
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
    day_open_only_rate: float | None = typer.Option(
        None,
        help="Chance of opening-only on non-empty days (ignored for event days).",
    ),
    response_rate: float | None = typer.Option(None, help="Chance of responses."),
    min_tags: int | None = typer.Option(None, help="Minimum tags per entry."),
    max_tags: int | None = typer.Option(None, help="Max tags per entry."),
    timezone: str | None = typer.Option(
        None, help="IANA timezone (e.g. Europe/Amsterdam)."
    ),
    seed: int | None = typer.Option(None, help="Random seed."),
    persona: str | None = typer.Option(None, help="Persona hint for entries."),
    llm_max_tokens: int | None = typer.Option(None, help="Max tokens per entry."),
    entry_temperature: float | None = typer.Option(
        None, help="Entry temperature override."
    ),
    small_entry_rate: float | None = typer.Option(
        None, help="Probability of small (tweet-like) entries."
    ),
    medium_entry_rate: float | None = typer.Option(
        None, help="Probability of medium entries."
    ),
    large_entry_rate: float | None = typer.Option(
        None, help="Probability of large entries."
    ),
    entry_context_size: int | None = typer.Option(
        None, help="Recent entries to include."
    ),
    entry_context_chars: int | None = typer.Option(
        None, help="Chars per context snippet."
    ),
    min_entry_chars: int | None = typer.Option(None, help="Minimum entry length."),
    max_entry_chars: int | None = typer.Option(None, help="Maximum entry length."),
    entry_retries: int | None = typer.Option(None, help="Retries for short entries."),
    day_empty_rate: float | None = typer.Option(
        None,
        help="Chance of no opening and no entries (ignored for event days).",
    ),
    story_events: int | None = typer.Option(None, help="Narrative event count."),
    story_followup_rate: float | None = typer.Option(
        None, help="Chance of follow-up days."
    ),
    story_intensity: float | None = typer.Option(
        None, help="Event injection strength."
    ),
    story_allow_overlap: bool | None = typer.Option(None, help="Allow event overlap."),
    multi_response_rate: float | None = typer.Option(
        None, help="Chance of multi responses."
    ),
    max_responses_per_entry: int | None = typer.Option(
        None, help="Max responses per entry."
    ),
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
        "day_open_only_rate": day_open_only_rate,
        "response_rate": response_rate,
        "min_tags": min_tags,
        "max_tags": max_tags,
        "timezone": timezone,
        "seed": seed,
        "persona": persona,
        "llm_max_tokens": llm_max_tokens,
        "entry_temperature": entry_temperature,
        "small_entry_rate": small_entry_rate,
        "medium_entry_rate": medium_entry_rate,
        "large_entry_rate": large_entry_rate,
        "entry_context_size": entry_context_size,
        "entry_context_chars": entry_context_chars,
        "min_entry_chars": min_entry_chars,
        "max_entry_chars": max_entry_chars,
        "entry_retries": entry_retries,
        "day_empty_rate": day_empty_rate,
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

#  LLAMORA_LLM__UPSTREAM__HOST=http://localhost:8080   uv run python scripts/generate_demo_data.py  \
#      --config config/demo_data.example.toml \
#      --username demo_user \
#      --password 'demo_user_test_password12345!' \
#      --start-date 2025-06-01 \
#      --end-date 2026-02-10
#
#  with: llama-server --model /media/array/Models/GGUF/Meta-Llama-3.1-8B-Instruct-Q6_K.gguf -c 18000 -ngl 999  --parallel 1 -fa on --jinja
