"""Entry prompt assembly helpers."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Any, Iterable, Mapping, Sequence

from llamora.app.services.time import humanize

from .prompt_templates import render_prompt_template
from .tokenizers.tokenizer import estimate_tokens


@dataclass(frozen=True, slots=True)
class EntryPromptSeries:
    """Collection of token estimates for the base and history suffixes."""

    base_tokens: int
    suffix_tokens: tuple[int, ...]

    @property
    def base_token_count(self) -> int:
        return self.base_tokens

    @property
    def suffix_token_counts(self) -> tuple[int, ...]:
        return self.suffix_tokens


def _normalise_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_entry_messages(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> list[dict[str, str]]:
    normalised: list[dict[str, str]] = []
    for raw in messages:
        role = _normalise_text(raw.get("role")) or "user"
        content_source = raw.get("content")
        if content_source is None:
            content_source = raw.get("text")
        content = _normalise_text(content_source)
        normalised.append(
            {
                "role": role,
                "content": content,
            }
        )
    return normalised


def _serialize_messages_for_estimate(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    add_generation_prompt: bool = True,
) -> str:
    parts: list[str] = []
    for message in _coerce_entry_messages(messages):
        role = _normalise_text(message.get("role")) or "user"
        content = _normalise_text(message.get("content"))
        if content:
            parts.append(f"{role}:\n{content}")
        else:
            parts.append(f"{role}:")
    if add_generation_prompt:
        parts.append("assistant:")
    return "\n\n".join(parts).strip()


def estimate_entry_messages_tokens(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    add_generation_prompt: bool = True,
) -> int:
    serialized = _serialize_messages_for_estimate(
        messages, add_generation_prompt=add_generation_prompt
    )
    return estimate_tokens(serialized)


def _context_lines(date: str | None, part_of_day: str | None) -> list[str]:
    lines: list[str] = []
    if date and part_of_day:
        lines.append(f"Today is the {date}, during the {part_of_day}.")
    elif date:
        lines.append(f"Today is the {date}.")
    elif part_of_day:
        lines.append(f"It is currently the {part_of_day}.")
    return lines


def _format_yesterday_messages(
    yesterday_messages: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> Iterable[str]:
    for humanized, grouped in groupby(
        yesterday_messages, key=lambda message: humanize(message["created_at"])
    ):
        yield humanized
        for message in grouped:
            role = "You" if message.get("role") == "assistant" else "user"
            text = _normalise_text(message.get("text"))
            if text:
                yield f"({role}) {text}"
            else:
                yield f"({role})"
        yield ""


def _build_system_message(
    *,
    date: str | None = None,
    part_of_day: str | None = None,
    history: Sequence[Mapping[str, Any] | dict[str, Any]] = (),
) -> str:
    context_lines = _context_lines(date, part_of_day)
    rendered = render_prompt_template(
        "system.txt.j2",
        context_lines=context_lines,
    )
    return rendered.strip()


def _build_opening_system_message(
    yesterday_messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    date: str | None = None,
    part_of_day: str | None = None,
    is_new: bool = False,
    has_no_activity: bool = False,
) -> str:
    context_lines = _context_lines(date, part_of_day)
    rendered = render_prompt_template(
        "opening_system.txt.j2",
        context_lines=context_lines,
        is_new=is_new,
        has_no_activity=has_no_activity,
    )
    return rendered.strip()


def _build_opening_recap_message(
    yesterday_messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    is_new: bool,
    has_no_activity: bool,
) -> str:
    recap_lines = list(_format_yesterday_messages(yesterday_messages))
    rendered = render_prompt_template(
        "opening_recap.txt.j2",
        is_new=is_new,
        has_no_activity=has_no_activity,
        recap_lines=recap_lines,
    )
    return rendered.strip()


def build_entry_messages(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    **context: Any,
) -> list[dict[str, str]]:
    """Return entry messages representing ``history`` and ``context``."""

    system_message = _build_system_message(
        date=_normalise_text(context.get("date")) or None,
        part_of_day=_normalise_text(context.get("part_of_day")) or None,
        history=history,
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_message}]

    for entry in history:
        role = _normalise_text(entry.get("role")) or "user"
        content = _normalise_text(entry.get("text"))
        messages.append({"role": role, "content": content})

    return messages


def build_opening_messages(
    yesterday_messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    **context: Any,
) -> list[dict[str, str]]:
    """Return entry messages for the automated opening greeting."""

    is_new = bool(context.get("is_new"))
    has_no_activity = bool(context.get("has_no_activity"))
    system_message = _build_opening_system_message(
        yesterday_messages,
        date=_normalise_text(context.get("date")) or None,
        part_of_day=_normalise_text(context.get("part_of_day")) or None,
        is_new=is_new,
        has_no_activity=has_no_activity,
    )
    recap_message = _build_opening_recap_message(
        yesterday_messages,
        is_new=is_new,
        has_no_activity=has_no_activity,
    )

    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": recap_message},
        {"role": "assistant", "content": ""},
    ]


def render_entry_prompt_series(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    **context: Any,
) -> EntryPromptSeries:
    """Return token estimates for the base system message and each suffix."""

    ctx_history = list(history)
    base_messages = build_entry_messages((), **context)
    base_tokens = estimate_entry_messages_tokens(base_messages)

    suffix_tokens: list[int] = []
    for idx in range(len(ctx_history)):
        suffix_history = ctx_history[idx:]
        messages = build_entry_messages(suffix_history, **context)
        suffix_tokens.append(estimate_entry_messages_tokens(messages))

    return EntryPromptSeries(base_tokens=base_tokens, suffix_tokens=tuple(suffix_tokens))
