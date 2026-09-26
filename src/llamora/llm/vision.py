"""Whether replies show the model the entries' images (``LLM.vision``).

``enabled`` is ``"auto"`` (the default), ``true`` or ``false``:

* auto: send images when the model server says it can see them. llama.cpp's
  ``/props`` reports ``modalities.vision``; a hosted API has no ``/props``,
  so auto means off there and images never leave the machine unasked.
* true: always send them (e.g. a hosted vision model).
* false: never send them.

Images that aren't sent are still mentioned to the model in text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

VisionMode = Literal["auto", "on", "off"]

_TRUE = {"true", "on", "yes", "1"}
_FALSE = {"false", "off", "no", "0"}


def parse_vision_mode(value: Any) -> VisionMode | None:
    """The mode for a configured value, or None when it isn't one."""

    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value if value is not None else "auto").strip().lower()
    if text in ("", "auto"):
        return "auto"
    if text in _TRUE:
        return "on"
    if text in _FALSE:
        return "off"
    return None


def props_have_vision(props: Mapping[str, Any] | None) -> bool:
    """Whether llama.cpp's ``/props`` says the loaded model can see images."""

    modalities = (props or {}).get("modalities")
    return isinstance(modalities, Mapping) and bool(modalities.get("vision"))


def vision_enabled(mode: VisionMode, props: Mapping[str, Any] | None) -> bool:
    if mode == "on":
        return True
    if mode == "off":
        return False
    return props_have_vision(props)


@dataclass(frozen=True, slots=True)
class VisionConfig:
    mode: VisionMode = "auto"
    max_images: int = 8
    max_edge: int = 1024
    quality: int = 85
    tokens_per_image: int = 300

    @classmethod
    def from_settings(cls, settings: Any) -> VisionConfig:
        raw = settings.get("LLM.vision") or {}
        max_images = raw.get("max_images")
        if max_images is None:
            # Unset: as many as an entry can have, so a full entry is seen whole.
            max_images = settings.get("IMAGES.max_per_entry", 8)
        return cls(
            mode=parse_vision_mode(raw.get("enabled")) or "auto",
            max_images=max(0, int(max_images)),
            max_edge=max(64, int(raw.get("max_edge", 1024))),
            quality=min(100, max(1, int(raw.get("quality", 85)))),
            tokens_per_image=max(1, int(raw.get("tokens_per_image", 300))),
        )
