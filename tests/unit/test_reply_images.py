"""Entry images in reply prompts (llm.entry_template, LLMClient payloads)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from llamora.llm.client import LLMClient
from llamora.llm.entry_template import (
    IMAGE_REF,
    ImagePolicy,
    build_entry_messages,
    estimate_entry_messages_tokens,
    select_images,
)

SEND = ImagePolicy(send=True, max_images=4)


def entry(text: str, *ids: str, role: str = "user") -> dict:
    return {"role": role, "text": text, "images": [{"id": i} for i in ids]}


def user_turns(history, policy=None) -> list:
    messages = build_entry_messages(history, image_policy=policy, date="1st of May")
    return [m["content"] for m in messages if m["role"] == "user"]


def refs(content) -> list[str]:
    return [p["image_id"] for p in content if p.get("type") == IMAGE_REF]


def texts(content) -> list[str]:
    return [p["text"] for p in content if p.get("type") == "text"]


# -- when images are not sent (the default) ------------------------------------


def test_entries_without_images_are_plain_text() -> None:
    assert user_turns([entry("Just words")], SEND) == ["Just words"]


def test_unsent_images_are_mentioned() -> None:
    (content,) = user_turns([entry("At the harbour", "a", "b")])
    assert content == (
        "At the harbour\n\n"
        "[The writer attached 2 photos to this entry; you can't see them.]"
    )


def test_an_entry_of_only_images_is_never_empty() -> None:
    (content,) = user_turns([entry("", "a")])
    assert content == "[The writer attached 1 photo to this entry; you can't see it.]"


# -- when images are sent -------------------------------------------------------


def test_sent_images_become_references_after_the_text() -> None:
    (content,) = user_turns([entry("At the harbour", "a", "b")], SEND)
    assert texts(content) == ["At the harbour"]
    assert refs(content) == ["a", "b"]
    assert content[0]["type"] == "text"  # text first, then the images


def test_an_entry_of_only_images_sends_just_the_images() -> None:
    (content,) = user_turns([entry("", "a")], SEND)
    assert content == [{"type": IMAGE_REF, "image_id": "a"}]


def test_the_replied_to_entry_comes_first_then_the_newest_earlier_images() -> None:
    history = [
        entry("Morning", "m1", "m2"),
        entry("A reply", role="assistant"),
        entry("Noon", "n1", "n2"),
        entry("Evening", "e1", "e2"),  # being replied to
    ]
    assert select_images(history, 4) == {"e1", "e2", "n2", "n1"}
    morning, noon, evening = user_turns(history, SEND)
    assert refs(evening) == ["e1", "e2"]
    assert refs(noon) == ["n1", "n2"]  # in their own order within the entry
    assert (
        morning
        == "Morning\n\n[The writer attached 2 photos to this entry; you can't see them.]"
    )


def test_images_beyond_the_limit_are_noted() -> None:
    history = [entry("Many", "a", "b", "c", "d", "e", "f")]
    (content,) = user_turns(history, ImagePolicy(send=True, max_images=4))
    assert refs(content) == ["a", "b", "c", "d"]
    assert texts(content)[-1] == "[2 more photos attached to this entry, not shown.]"


def test_a_limit_of_zero_sends_none() -> None:
    (content,) = user_turns([entry("Words", "a")], ImagePolicy(send=True, max_images=0))
    assert isinstance(content, str) and "you can't see it" in content


def test_assistant_entries_are_untouched() -> None:
    history = [entry("A reply", "x", role="assistant"), entry("Mine")]
    messages = build_entry_messages(history, image_policy=SEND)
    assert [m["content"] for m in messages[1:]] == ["A reply", "Mine"]


def test_token_estimates_count_image_turns_without_failing() -> None:
    history = [entry("At the harbour", "a")]
    with_images = build_entry_messages(history, image_policy=SEND)
    assert estimate_entry_messages_tokens(with_images) > 0


# -- the client ----------------------------------------------------------------


def client_stub() -> SimpleNamespace:
    stub = SimpleNamespace(logger=logging.getLogger("test"))
    stub._without_image_refs = lambda parts: LLMClient._without_image_refs(stub, parts)
    return stub


def test_unresolved_references_never_reach_the_model_server(caplog) -> None:
    messages = build_entry_messages([entry("Look", "a", "b")], image_policy=SEND)
    with caplog.at_level(logging.ERROR):
        payload = LLMClient._build_chat_payload(client_stub(), messages, {})
    user = payload["messages"][-1]["content"]
    assert all(part["type"] == "text" for part in user)
    assert user[-1]["text"].endswith("you can't see them.]")
    assert "unresolved image reference" in caplog.text


def test_prompt_logs_show_images_as_placeholders(caplog) -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,/9j/SECRET"},
                },
            ],
        },
    ]
    with caplog.at_level(logging.DEBUG, logger="llamora.llm.client"):
        LLMClient._log_prompt(client_stub(), "entry-1", messages, {})
    assert "Look" in caplog.text and "[image]" in caplog.text
    assert "SECRET" not in caplog.text and "base64" not in caplog.text
