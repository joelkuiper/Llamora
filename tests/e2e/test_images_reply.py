"""Replying to entries with images (the model server sees what it gets).

Until vision support is switched on, the model is told photos are attached
but never receives them, and an entry of only images is never empty.
"""

from __future__ import annotations

import json
from datetime import date

from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import ApiClient, wait_for_app
from imaging import encode, halves
from timekit import reply_requests

Session = tuple[Page, ApiClient]
PHOTO = encode(halves((300, 200)), "PNG")


def today(page: Page) -> date:
    return date.fromisoformat(page.locator("#entries").get_attribute("data-date") or "")


def respond(page: Page, entry_id: str, fake_llm: FakeLLM) -> dict:
    before = len(reply_requests(fake_llm))
    entry = page.locator(f"#entry-{entry_id}")
    entry.get_by_role("button", name="Respond").click()
    expect(page.locator(f"#entry-responses-{entry_id}")).to_contain_text(fake_llm.reply)
    wait_for_app(page)
    requests = reply_requests(fake_llm)
    assert len(requests) > before, "no reply request reached the model server"
    return requests[-1]


def last_user_turn(request: dict):
    return [m for m in request["messages"] if m["role"] == "user"][-1]["content"]


def test_the_model_is_told_about_photos_it_cannot_see(
    fresh_session: Session, fake_llm: FakeLLM
) -> None:
    page, api = fresh_session
    ids = [api.upload_image_id(PHOTO) for _ in range(2)]
    entry_id = api.create_entry(today(page), "At the harbour", image_ids=ids)
    page.reload()
    wait_for_app(page)

    request = respond(page, entry_id, fake_llm)

    assert last_user_turn(request) == (
        "At the harbour\n\n"
        "[The writer attached 2 photos to this entry; you can't see them.]"
    )
    body = json.dumps(request)
    assert "image_ref" not in body and "data:image" not in body


def test_an_entry_of_only_images_gets_a_reply(
    fresh_session: Session, fake_llm: FakeLLM
) -> None:
    page, api = fresh_session
    image_id = api.upload_image_id(PHOTO)
    entry_id = api.create_entry(today(page), "", image_ids=[image_id])
    page.reload()
    wait_for_app(page)

    request = respond(page, entry_id, fake_llm)

    assert last_user_turn(request) == (
        "[The writer attached 1 photo to this entry; you can't see it.]"
    )


def test_earlier_entries_mention_their_photos_too(
    fresh_session: Session, fake_llm: FakeLLM
) -> None:
    page, api = fresh_session
    day = today(page)
    api.create_entry(day, "Morning walk", image_ids=[api.upload_image_id(PHOTO)])
    entry_id = api.create_entry(day, "Evening, no pictures")
    page.reload()
    wait_for_app(page)

    request = respond(page, entry_id, fake_llm)

    user_turns = [m["content"] for m in request["messages"] if m["role"] == "user"]
    assert user_turns[-1] == "Evening, no pictures"
    assert any(
        t.startswith("Morning walk") and "1 photo" in t for t in user_turns[:-1]
    ), user_turns
