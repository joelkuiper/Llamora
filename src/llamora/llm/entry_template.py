"""Entry prompt assembly helpers backed by the tokenizer's chat template."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Any, Iterable, Mapping, Sequence, cast

from llamora.app.services.time import humanize

from .prompt_templates import render_prompt_template
from .tokenizers.tokenizer import get_tokenizer


@dataclass(frozen=True, slots=True)
class EntryPromptRender:
    """Container for a rendered prompt and its tokenisation."""

    prompt: str
    tokens: tuple[int, ...]

    @property
    def token_count(self) -> int:
        return len(self.tokens)


@dataclass(frozen=True, slots=True)
class EntryPromptSeries:
    """Collection of rendered prompts for the base and history suffixes."""

    base: EntryPromptRender
    suffixes: tuple[EntryPromptRender, ...]

    @property
    def base_token_count(self) -> int:
        return self.base.token_count

    @property
    def suffix_token_counts(self) -> tuple[int, ...]:
        return tuple(render.token_count for render in self.suffixes)


def _normalise_tokens(raw: Any) -> tuple[int, ...]:
    sequence: Any
    if isinstance(raw, (list, tuple)):
        sequence = raw
    elif isinstance(raw, Mapping):
        if "input_ids" in raw:
            sequence = raw["input_ids"]
        elif "ids" in raw:
            sequence = raw["ids"]
        else:  # pragma: no cover - defensive
            raise TypeError("Tokenizer.apply_chat_template returned unsupported token data")
    elif hasattr(raw, "input_ids"):
        sequence = getattr(raw, "input_ids")
    elif hasattr(raw, "tolist"):
        sequence = raw.tolist()
    else:  # pragma: no cover - defensive
        raise TypeError("Tokenizer.apply_chat_template returned unsupported token data")

    if isinstance(sequence, (list, tuple)) and sequence:
        first = sequence[0]
        if isinstance(first, (list, tuple)):
            if len(sequence) != 1:  # pragma: no cover - defensive
                raise TypeError("Tokenizer tokens must be a single sequence of integers")
            sequence = first
    elif hasattr(sequence, "tolist"):
        sequence = sequence.tolist()

    try:
        return tuple(int(token) for token in sequence)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise TypeError("Tokenizer tokens must be integers") from exc


def _coerce_entry_messages(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> list[dict[str, str]]:
    normalised: list[dict[str, str]] = []
    for raw in messages:
        data = cast(Mapping[str, Any], raw)
        role = _normalise_text(data.get("role"))
        content_source = data.get("content")
        if content_source is None:
            content_source = data.get("text")
        content = _normalise_text(content_source)
        normalised.append(
            {
                "role": role or "user",
                "content": content,
            }
        )
    return normalised


def _render_entry_prompt(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    add_generation_prompt: bool = True,
) -> EntryPromptRender:
    tokenizer = get_tokenizer()
    message_list = _coerce_entry_messages(messages)

    prompt = tokenizer.apply_chat_template(
        message_list,
        tokenize=False,
        add_generation_prompt=add_generation_prompt,
    )
    if not isinstance(prompt, str):  # pragma: no cover - defensive
        raise TypeError("Tokenizer.apply_chat_template returned unexpected output")

    token_data = tokenizer.apply_chat_template(
        message_list,
        tokenize=True,
        add_generation_prompt=add_generation_prompt,
    )
    tokens = _normalise_tokens(token_data)
    return EntryPromptRender(prompt=prompt, tokens=tokens)


def _normalise_text(value: Any) -> str:
    return str(value or "").strip()


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


def render_entry_prompt(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> EntryPromptRender:
    """Render ``messages`` to a prompt using the tokenizer's chat template."""

    return _render_entry_prompt(messages)


def render_entry_prompt_series(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    **context: Any,
) -> EntryPromptSeries:
    """Render prompts for the base system message and each history suffix."""

    ctx_history = list(history)
    base_messages = build_entry_messages((), **context)
    base_render = _render_entry_prompt(base_messages)

    suffix_renders: list[EntryPromptRender] = []
    for idx in range(len(ctx_history)):
        suffix_history = ctx_history[idx:]
        messages = build_entry_messages(suffix_history, **context)
        suffix_renders.append(_render_entry_prompt(messages))

    return EntryPromptSeries(base=base_render, suffixes=tuple(suffix_renders))
