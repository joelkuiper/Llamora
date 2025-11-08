"""Chat prompt assembly helpers backed by the tokenizer's chat template."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Any, Iterable, Mapping, Sequence, cast

from llamora.app.services.time import humanize

from .tokenizers.tokenizer import format_vibes_text, get_tokenizer

from textwrap import dedent

SYSTEM_PROSE = dedent("""
    You are Llamora.
    You blend empathy with intelligence and a gentle sense of humor that never tries too hard.
    Your presence feels like spending time with someone who notices everything, but prefers to make sense of it with a smile.
    You listen closely, remember what matters, and weave those threads back into the conversation so it feels natural and alive.
    You share ideas with warmth and clarity, sometimes teasing out a thought or turning it on its head just to see it from a new angle.
    Each exchange is a small story, leaving the user feeling understood and a little lighter than before.
""").strip()


ANSWER_REQUIREMENTS = dedent("""
    Every answer must always have exactly two parts, in this order:
     - A natural language reply to the user.
     - Immediately after, output the tag `<meta>` followed by a single JSON object with the following shape: {"emoji":"…",' '"keywords":["#tag",…]} and close it with `</meta>`.'
""").strip()


@dataclass(frozen=True, slots=True)
class ChatPromptRender:
    """Container for a rendered prompt and its tokenisation."""

    prompt: str
    tokens: tuple[int, ...]

    @property
    def token_count(self) -> int:
        return len(self.tokens)


@dataclass(frozen=True, slots=True)
class ChatPromptSeries:
    """Collection of rendered prompts for the base and history suffixes."""

    base: ChatPromptRender
    suffixes: tuple[ChatPromptRender, ...]

    @property
    def base_token_count(self) -> int:
        return self.base.token_count

    @property
    def suffix_token_counts(self) -> tuple[int, ...]:
        return tuple(render.token_count for render in self.suffixes)


def _normalise_tokens(raw: Any) -> tuple[int, ...]:
    if isinstance(raw, (list, tuple)):
        sequence = raw
    elif hasattr(raw, "tolist"):
        sequence = raw.tolist()
    else:  # pragma: no cover - defensive
        raise TypeError("Tokenizer.apply_chat_template returned unsupported token data")

    try:
        return tuple(int(token) for token in sequence)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise TypeError("Tokenizer tokens must be integers") from exc


def _coerce_chat_messages(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> list[dict[str, str]]:
    normalised: list[dict[str, str]] = []
    for raw in messages:
        data = cast(Mapping[str, Any], raw)
        role = _normalise_text(data.get("role"))
        content_source = data.get("content")
        if content_source is None:
            content_source = data.get("message")
        content = _normalise_text(content_source)
        normalised.append(
            {
                "role": role or "user",
                "content": content,
            }
        )
    return normalised


def _render_chat_prompt(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    add_generation_prompt: bool = True,
) -> ChatPromptRender:
    tokenizer = get_tokenizer()
    message_list = _coerce_chat_messages(messages)

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
    return ChatPromptRender(prompt=prompt, tokens=tokens)


def _normalise_text(value: Any) -> str:
    return str(value or "").strip()


def _conversation_vibes(history: Sequence[Mapping[str, Any] | dict[str, Any]]) -> str:
    line = format_vibes_text(history)
    return line.strip()


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
            text = _normalise_text(message.get("message"))
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
    lines: list[str] = [SYSTEM_PROSE, ""]
    lines.extend(_context_lines(date, part_of_day))

    vibes_line = _conversation_vibes(history)
    if vibes_line:
        lines.extend(["", vibes_line])

    lines.extend(["", ANSWER_REQUIREMENTS])
    return "\n".join(lines).strip()


def _build_opening_system_message(
    yesterday_messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    date: str | None = None,
    part_of_day: str | None = None,
    is_new: bool = False,
    has_no_activity: bool = False,
) -> str:
    lines: list[str] = [SYSTEM_PROSE, ""]
    lines.extend(_context_lines(date, part_of_day))

    instruction: str
    if is_new:
        instruction = (
            "Compose a warm welcome for a first-time user. Offer a gentle "
            "invitation to start the conversation."
        )
        lines.extend(["", instruction])
    elif has_no_activity:
        instruction = (
            "The user had no activity yesterday. Greet them softly and invite "
            "them to begin today's conversation."
        )
        lines.extend(["", instruction])
    else:
        lines.extend(
            [
                "",
                "Compose a single calm greeting that summarizes yesterday for "
                "the user in a few sentences.",
                "",
                "Use the recap provided below to ground your greeting. If "
                "themes appear in the recap, gently acknowledge them without "
                "quoting or listing. Close with a soft invitation to begin.",
                "",
            ]
        )

    lines.extend(["", ANSWER_REQUIREMENTS])
    return "\n".join(lines).strip()


def _build_opening_recap_message(
    yesterday_messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    is_new: bool,
    has_no_activity: bool,
) -> str:
    if is_new:
        return (
            "Yesterday recap:\n"
            "- The user is beginning their first conversation with you."
        )

    if has_no_activity:
        return (
            "Yesterday recap:\n"
            "- The user had no activity yesterday, so there are no messages to review."
        )

    recap_lines = list(_format_yesterday_messages(yesterday_messages))
    if not recap_lines:
        return "Yesterday recap:\n- No messages were captured yesterday."

    lines = ["Yesterday recap:", ""]
    lines.extend(recap_lines)
    return "\n".join(lines).strip()


def build_chat_messages(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    **context: Any,
) -> list[dict[str, str]]:
    """Return chat messages representing ``history`` and ``context``."""

    system_message = _build_system_message(
        date=_normalise_text(context.get("date")) or None,
        part_of_day=_normalise_text(context.get("part_of_day")) or None,
        history=history,
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_message}]

    for entry in history:
        role = _normalise_text(entry.get("role")) or "user"
        content = _normalise_text(entry.get("message"))
        messages.append({"role": role, "content": content})

    return messages


def build_opening_messages(
    yesterday_messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    **context: Any,
) -> list[dict[str, str]]:
    """Return chat messages for the automated opening greeting."""

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


def render_chat_prompt(messages: Sequence[Mapping[str, Any] | dict[str, Any]]) -> str:
    """Render ``messages`` to a prompt using the tokenizer's chat template."""

    return _render_chat_prompt(messages).prompt


def render_chat_prompt_series(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    **context: Any,
) -> ChatPromptSeries:
    """Render prompts for the base system message and each history suffix."""

    ctx_history = list(history)
    base_messages = build_chat_messages((), **context)
    base_render = _render_chat_prompt(base_messages)

    suffix_renders: list[ChatPromptRender] = []
    for idx in range(len(ctx_history)):
        suffix_history = ctx_history[idx:]
        messages = build_chat_messages(suffix_history, **context)
        suffix_renders.append(_render_chat_prompt(messages))

    return ChatPromptSeries(base=base_render, suffixes=tuple(suffix_renders))
