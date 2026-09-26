"""Care guidance in prompts and the configured support resources."""

from __future__ import annotations

import copy
from collections.abc import Iterator

import pytest

from llamora.app.services.config_validation import _validate_support
from llamora.llm.entry_template import build_entry_messages, build_opening_messages
from llamora.llm.support import SupportResource, parse_resources, support_resources
from llamora.settings import settings

NL = {
    "name": "113 Zelfmoordpreventie",
    "phone": "0800-0113",
    "url": "https://www.113.nl",
    "note": "Free and anonymous, day and night.",
}


@pytest.fixture
def support_settings() -> Iterator[None]:
    original = copy.deepcopy(settings.get("SUPPORT").to_dict())
    yield
    settings.set("SUPPORT", original, merge=False)


def use_resources(resources) -> None:
    settings.set("SUPPORT", {"resources": resources}, merge=False)


def reply_system_prompt() -> str:
    messages = build_entry_messages([{"role": "user", "text": "Hi"}], date="1st of May")
    return messages[0]["content"]


def opening_system_prompt() -> str:
    return build_opening_messages([], date="1st of May", part_of_day="morning")[0][
        "content"
    ]


def test_the_default_is_an_international_directory() -> None:
    assert support_resources() == [
        SupportResource(
            name="Find A Helpline",
            url="https://findahelpline.com",
            note="Free, confidential support lines in your country, by phone, text or chat.",
        )
    ]


def test_resources_need_a_name_and_a_way_to_reach_them() -> None:
    assert parse_resources(
        [NL, {"name": "No contact"}, {"url": "https://x.example"}, "junk", None]
    ) == [SupportResource(**NL)]


@pytest.mark.parametrize("prompt", [reply_system_prompt, opening_system_prompt])
def test_every_prompt_carries_the_care_guidance(prompt) -> None:
    text = prompt()
    assert "**When things are hard**" in text
    assert "whether they are safe" in text
    assert "stay with them as you normally would" in text
    # Grief is met with presence, not treated as an emergency.
    assert "this is not an emergency" in text


def test_the_model_may_only_name_configured_resources(support_settings) -> None:
    use_resources([NL])
    text = reply_system_prompt()
    assert "- 113 Zelfmoordpreventie: 0800-0113 (https://www.113.nl)" in text
    assert "use only these (never any other number or service)" in text
    assert "findahelpline" not in text


def test_without_resources_the_model_is_told_not_to_invent_any(
    support_settings,
) -> None:
    use_resources([])
    text = reply_system_prompt()
    assert "Never invent phone numbers or services." in text
    assert "use only these" not in text


def test_a_reply_answers_a_crisis_as_it_happens() -> None:
    text = reply_system_prompt()
    assert "ask whether they are safe right now" in text
    assert "It is a new morning" not in text


def test_the_next_morning_checks_in_instead_of_cheering_up() -> None:
    text = opening_system_prompt()
    # Not as if it had just been written: a new morning, no echoing.
    assert "It is a new morning, so don't respond as if they just wrote it" in text
    assert "ask whether they are safe right now" not in text
    assert "do not summarise it or try to lift the mood" in text
    assert "check in gently and plainly about how they are today" in text


def test_support_settings_are_validated(support_settings) -> None:
    use_resources([NL])
    assert list(_validate_support()) == []
    use_resources([NL, {"name": "Unreachable"}])
    assert list(_validate_support()) == [
        "Each SUPPORT.resources entry needs a name and a phone and/or url."
    ]
