"""Where to find help (``SUPPORT.resources``), for prompts and the UI.

The model only ever mentions resources listed here, so it never invents a
phone number; the same list is always reachable from the header.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from llamora.settings import settings


@dataclass(frozen=True, slots=True)
class SupportResource:
    name: str
    phone: str = ""
    url: str = ""
    note: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def parse_resources(raw: Any) -> list[SupportResource]:
    """Configured resources that have a name and a way to reach them."""

    resources: list[SupportResource] = []
    for item in raw or []:
        if not isinstance(item, Mapping):
            continue
        resource = SupportResource(
            name=_text(item.get("name")),
            phone=_text(item.get("phone")),
            url=_text(item.get("url")),
            note=_text(item.get("note")),
        )
        if resource.name and (resource.phone or resource.url):
            resources.append(resource)
    return resources


def support_resources() -> list[SupportResource]:
    return parse_resources(settings.get("SUPPORT.resources"))
