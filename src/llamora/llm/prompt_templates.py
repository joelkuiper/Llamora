"""Utilities for rendering LLM prompt templates using Jinja2."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from llamora.settings import CONFIG_DIR, PACKAGE_DIR, PROJECT_ROOT, settings

_DEFAULT_TEMPLATE_DIR = (PACKAGE_DIR / "llm" / "templates").resolve()


def _candidate_template_dirs(configured: str | None) -> list[Path]:
    candidates: list[Path] = []
    if configured:
        candidate_path = Path(configured).expanduser()
        if candidate_path.is_absolute():
            candidates.append(candidate_path)
        else:
            candidates.extend(
                [
                    Path.cwd() / candidate_path,
                    PROJECT_ROOT / candidate_path,
                    CONFIG_DIR / candidate_path,
                ]
            )
    candidates.append(_DEFAULT_TEMPLATE_DIR)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _resolve_template_dir() -> Path:
    configured = getattr(settings.PROMPTS, "template_dir", "")
    configured_str = str(configured).strip() if configured else ""
    for candidate in _candidate_template_dirs(configured_str or None):
        if candidate.is_dir():
            return candidate
    searched = ", ".join(
        str(path) for path in _candidate_template_dirs(configured_str or None)
    )
    raise FileNotFoundError(f"Unable to locate prompt templates. Searched: {searched}")


@lru_cache(maxsize=1)
def get_environment() -> Environment:
    """Return a cached Jinja2 environment configured for prompt rendering."""

    template_dir = _resolve_template_dir()
    loader = FileSystemLoader(str(template_dir))
    return Environment(
        loader=loader,
        autoescape=False,
        keep_trailing_newline=True,
    )


def get_prompt_template(name: str):
    """Return a compiled prompt template by ``name``."""

    environment = get_environment()
    try:
        return environment.get_template(name)
    except TemplateNotFound as exc:  # pragma: no cover - defensive
        loader = environment.loader
        search_path = getattr(loader, "searchpath", None)
        if search_path is None:
            location = "<unknown>"
        else:
            location = ", ".join(str(path) for path in search_path)
        raise FileNotFoundError(
            f"Prompt template '{name}' could not be located in {location}"
        ) from exc


def render_prompt_template(name: str, **context: Any) -> str:
    """Render the prompt template ``name`` with ``context``."""

    template = get_prompt_template(name)
    rendered = template.render(**context)
    return rendered


__all__ = ["get_environment", "get_prompt_template", "render_prompt_template"]
