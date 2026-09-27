"""Tag suggestions (generate_metadata) see an entry's images when allowed."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from llamora.app.services.entry_metadata import (
    DEFAULT_METADATA_EMOJI,
    generate_metadata,
)
from llamora.llm.entry_template import IMAGE_REF, ImagePolicy


class FakeLLM:
    def __init__(self, *, send: bool, max_images: int = 8) -> None:
        self.image_policy = ImagePolicy(send=send, max_images=max_images)
        self.calls: list[dict[str, Any]] = []

    async def complete_messages(self, messages, *, params=None, image_resolver=None):
        self.calls.append({"messages": messages, "resolver": image_resolver})
        return json.dumps({"emoji": "📷", "tags": ["harbour", "boats"]})


async def resolver(image_id: str) -> str:
    return f"data:image/jpeg;base64,{image_id}"


def run(llm: FakeLLM, text: str, image_ids=(), with_resolver: bool = True):
    return asyncio.run(
        generate_metadata(
            llm,
            text,
            image_ids=image_ids,
            image_resolver=resolver if with_resolver else None,
        )
    )


def user_content(llm: FakeLLM):
    (call,) = llm.calls
    return call["messages"][-1]["content"]


def test_images_are_shown_after_the_text() -> None:
    llm = FakeLLM(send=True)
    result = run(llm, "At the harbour", ["a", "b"])

    assert result == {"emoji": "📷", "tags": ["harbour", "boats"]}
    assert user_content(llm) == [
        {"type": "text", "text": "At the harbour"},
        {"type": IMAGE_REF, "image_id": "a"},
        {"type": IMAGE_REF, "image_id": "b"},
    ]
    assert llm.calls[0]["resolver"] is resolver


def test_an_entry_of_only_images_gets_suggestions() -> None:
    llm = FakeLLM(send=True)
    result = run(llm, "", ["a"])
    assert result["tags"] == ["harbour", "boats"]
    assert user_content(llm) == [{"type": IMAGE_REF, "image_id": "a"}]


def test_at_most_max_images() -> None:
    llm = FakeLLM(send=True, max_images=2)
    run(llm, "Many", ["a", "b", "c"])
    refs = [p["image_id"] for p in user_content(llm) if p.get("type") == IMAGE_REF]
    assert refs == ["a", "b"]


def test_without_vision_it_is_text_only() -> None:
    llm = FakeLLM(send=False)
    run(llm, "At the harbour", ["a"])
    assert user_content(llm) == "At the harbour"
    assert llm.calls[0]["resolver"] is None


def test_without_a_resolver_it_is_text_only() -> None:
    llm = FakeLLM(send=True)
    run(llm, "At the harbour", ["a"], with_resolver=False)
    assert user_content(llm) == "At the harbour"


def test_only_images_without_vision_asks_nothing() -> None:
    llm = FakeLLM(send=False)
    result = run(llm, "", ["a"])
    assert result == {"emoji": DEFAULT_METADATA_EMOJI, "tags": []}
    assert llm.calls == []
