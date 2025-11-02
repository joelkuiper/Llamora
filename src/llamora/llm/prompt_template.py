"""Prompt rendering helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Template

from itertools import groupby

from llamora.app.services.time import humanize
from llamora.settings import settings
from llamora.util import resolve_data_path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_PROMPT_FILE = "llamora_chatml.j2"

__all__ = [
    "build_opening_prompt",
    "build_prompt",
]


def _resolve_prompt_path() -> Path:
    """Return the path to the configured prompt template."""

    configured = str(getattr(settings.PROMPTS, "prompt_file", "") or "").strip()
    if configured:
        fallback_name = Path(configured).name or DEFAULT_PROMPT_FILE
    else:
        configured = DEFAULT_PROMPT_FILE
        fallback_name = DEFAULT_PROMPT_FILE
    return resolve_data_path(
        configured,
        fallback_dir=PROMPTS_DIR,
        fallback_name=fallback_name,
    )


@lru_cache(maxsize=None)
def _load_template(template_path: str) -> Template:
    path = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(path.parent),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["humanize"] = humanize
    return env.get_template(path.name)


def _get_template() -> Template:
    path = _resolve_prompt_path()
    return _load_template(str(path))


def build_prompt(history: list[dict[str, Any]], **context: Any) -> str:
    """Render the main chat prompt using the configured template."""

    template = _get_template()
    return template.render(history=history, is_opening=False, **context)


def build_opening_prompt(
    yesterday_messages: list[dict[str, Any]], **context: Any
) -> str:
    """Render the opening prompt shown before any new messages."""

    grouped_messages = []
    for humanized, group in groupby(
        yesterday_messages,
        key=lambda message: humanize(message["created_at"]),
    ):
        grouped_messages.append(
            {
                "humanized": humanized,
                "messages": list(group),
            }
        )

    template = _get_template()
    return template.render(
        yesterday_message_groups=grouped_messages,
        history=[],
        is_opening=True,
        **context,
    )
