"""Entry prompt assembly helpers."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Any, Iterable, Mapping, Sequence

from llamora.app.services.time import humanize

from .prompt_templates import render_prompt_template
from .tokenizers.tokenizer import estimate_tokens


# A Llamora-internal content part: an entry image by id. The client resolves
# it to the image itself (an ``image_url`` part) just before sending, so
# prompts, token caches and logs only ever hold ids.
IMAGE_REF = "image_ref"


@dataclass(frozen=True, slots=True)
class ImagePolicy:
    """Whether entry images go to the model, and how many per reply."""

    send: bool = False
    max_images: int = 8
    # Prompt tokens one image costs (LLM.vision.tokens_per_image).
    tokens_per_image: int = 0


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


def content_text(content: Any) -> str:
    """The text of a message's content; images in a content list show as
    ``[image]`` (for estimates and logs, never the image data)."""

    if not isinstance(content, list):
        return _normalise_text(content)
    pieces: list[str] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text":
            pieces.append(_normalise_text(part.get("text")))
        elif part.get("type") in (IMAGE_REF, "image_url"):
            pieces.append("[image]")
    return "\n".join(piece for piece in pieces if piece)


def _coerce_entry_messages(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> list[dict[str, str]]:
    normalised: list[dict[str, str]] = []
    for raw in messages:
        role = _normalise_text(raw.get("role")) or "user"
        content_source = raw.get("content")
        if content_source is None:
            content_source = raw.get("text")
        content = content_text(content_source)
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


def count_images(messages: Sequence[Mapping[str, Any] | dict[str, Any]]) -> int:
    """Images in ``messages`` (references or resolved)."""

    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            total += sum(
                1
                for part in content
                if isinstance(part, Mapping)
                and part.get("type") in (IMAGE_REF, "image_url")
            )
    return total


def estimate_entry_messages_tokens(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    add_generation_prompt: bool = True,
    tokens_per_image: int = 0,
) -> int:
    """Estimated prompt tokens: the text, plus ``tokens_per_image`` for every
    image (a model decides the real cost; the setting approximates it)."""

    serialized = _serialize_messages_for_estimate(
        messages, add_generation_prompt=add_generation_prompt
    )
    return estimate_tokens(serialized) + tokens_per_image * count_images(messages)


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


def entry_image_ids(entry: Mapping[str, Any]) -> list[str]:
    """Ids of an entry's images, in order (history entries carry ``images``)."""

    ids: list[str] = []
    for image in entry.get("images") or []:
        image_id = image.get("id") if isinstance(image, Mapping) else image
        if image_id:
            ids.append(str(image_id))
    return ids


def select_images(
    history: Sequence[Mapping[str, Any] | dict[str, Any]], max_images: int
) -> set[str]:
    """The images a reply shows the model: the entry being answered (the last
    user entry) first, then the most recent images of earlier entries."""

    if max_images <= 0:
        return set()
    user_entries = [e for e in history if (e.get("role") or "user") == "user"]
    chosen: list[str] = []
    for entry in reversed(user_entries):
        ids = entry_image_ids(entry)
        # The replied-to entry in its own order; earlier ones newest first.
        ordered = ids if entry is user_entries[-1] else ids[::-1]
        for image_id in ordered:
            if len(chosen) >= max_images:
                return set(chosen)
            chosen.append(image_id)
    return set(chosen)


def _photos(count: int) -> str:
    return "1 photo" if count == 1 else f"{count} photos"


def photos_note(count: int, *, shown: int = 0) -> str:
    """What the model is told about images it doesn't get to see."""

    if shown:
        more = "1 more photo" if count == 1 else f"{count} more photos"
        return f"[{more} attached to this entry, not shown.]"
    pronoun = "it" if count == 1 else "them"
    return f"[The writer attached {_photos(count)} to this entry; you can't see {pronoun}.]"


def _user_content(
    entry: Mapping[str, Any], policy: ImagePolicy, shown: set[str]
) -> str | list[dict[str, Any]]:
    text = _normalise_text(entry.get("text"))
    ids = entry_image_ids(entry)
    if not ids:
        return text
    visible = [image_id for image_id in ids if policy.send and image_id in shown]
    hidden = len(ids) - len(visible)
    if not visible:
        note = photos_note(len(ids))
        return f"{text}\n\n{note}" if text else note
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend({"type": IMAGE_REF, "image_id": image_id} for image_id in visible)
    if hidden:
        parts.append({"type": "text", "text": photos_note(hidden, shown=len(visible))})
    return parts


def build_entry_messages(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    image_policy: ImagePolicy | None = None,
    **context: Any,
) -> list[dict[str, Any]]:
    """Return entry messages representing ``history`` and ``context``.

    Entries with images get either image references (``image_policy.send``;
    at most ``max_images`` per reply) or a note saying photos are attached,
    so an entry of only images never reaches the model as nothing.
    """

    policy = image_policy or ImagePolicy()
    shown = select_images(history, policy.max_images) if policy.send else set()

    system_message = _build_system_message(
        date=_normalise_text(context.get("date")) or None,
        part_of_day=_normalise_text(context.get("part_of_day")) or None,
        history=history,
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_message}]

    for entry in history:
        role = _normalise_text(entry.get("role")) or "user"
        if role == "user":
            content: str | list[dict[str, Any]] = _user_content(entry, policy, shown)
        else:
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
    # An ``image_policy`` in the context counts the images each suffix sends.
    policy = context.get("image_policy")
    per_image = policy.tokens_per_image if isinstance(policy, ImagePolicy) else 0
    base_messages = build_entry_messages((), **context)
    base_tokens = estimate_entry_messages_tokens(base_messages)

    suffix_tokens: list[int] = []
    for idx in range(len(ctx_history)):
        suffix_history = ctx_history[idx:]
        messages = build_entry_messages(suffix_history, **context)
        suffix_tokens.append(
            estimate_entry_messages_tokens(messages, tokens_per_image=per_image)
        )

    return EntryPromptSeries(
        base_tokens=base_tokens, suffix_tokens=tuple(suffix_tokens)
    )
