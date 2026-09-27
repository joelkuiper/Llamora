"""What a vision model is sent when replying to entries with images.

``LLM.vision.enabled`` is "auto" by default: images go to a llama.cpp server
whose /props reports vision, never unasked to a hosted API (no /props);
true/false force it. Replies are driven over HTTP against dedicated servers,
and the fake model server records exactly what it receives.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

import pytest

from fake_llm import FakeLLM
from harness import ApiClient, LiveServer, new_credentials, register_user, start_server
from imaging import decode, encode, halves
from timekit import reply_requests

TODAY = datetime.now(timezone.utc).date()
WIDE = encode(halves((3000, 1500), "red", "blue"), "JPEG")
SMALL = encode(halves((300, 200), "green", "white"), "PNG")


@pytest.fixture(scope="module")
def model() -> Iterator[FakeLLM]:
    fake = FakeLLM().start()
    yield fake
    fake.stop()


@pytest.fixture
def diary(
    model: FakeLLM, tmp_path_factory: pytest.TempPathFactory, assets: None
) -> Iterator[Callable[..., ApiClient]]:
    """A logged-in client on a fresh server whose model can (not) see."""
    servers: list[LiveServer] = []
    clients: list[ApiClient] = []

    def start(*, vision: bool, hosted: bool = False, env: dict[str, str] | None = None):
        model.reset()
        model.vision = vision
        model.llamacpp_endpoints = not hosted
        server = start_server(
            tmp_path_factory.mktemp("vision"), llm_url=model.url, extra_env=env
        )
        servers.append(server)
        client = ApiClient.logged_in(
            server.url, register_user(server.url, new_credentials("v"))
        )
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.close()
    for server in servers:
        server.stop()


def reply_to(client: ApiClient, model: FakeLLM, entry_id: str) -> dict:
    """Ask for a reply and return the request the model server received."""
    before = len(reply_requests(model))
    assert client.start_reply(entry_id, TODAY) == 200
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        requests = reply_requests(model)
        if len(requests) > before:
            client.stop_replies(entry_id)
            return requests[-1]
        time.sleep(0.05)
    raise AssertionError("no reply request reached the model server")


def user_turns(request: dict) -> list:
    return [m["content"] for m in request["messages"] if m["role"] == "user"]


def images_in(content) -> list:
    if not isinstance(content, list):
        return []
    prefix = "data:image/jpeg;base64,"
    found = []
    for part in content:
        if part.get("type") == "image_url":
            url = part["image_url"]["url"]
            assert url.startswith(prefix), url[:40]
            found.append(decode(base64.b64decode(url[len(prefix) :])))
    return found


def texts_in(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [part["text"] for part in content if part.get("type") == "text"]


def seed(client: ApiClient, text: str, *images: bytes) -> str:
    ids = [client.upload_image_id(data) for data in images]
    return client.create_entry(TODAY, text, image_ids=ids)


def test_a_local_vision_model_sees_the_images(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True)
    entry_id = seed(client, "At the harbour", WIDE, SMALL)

    content = user_turns(reply_to(client, model, entry_id))[-1]

    assert texts_in(content) == ["At the harbour"]
    wide, small = images_in(content)
    assert wide.format == "JPEG" and wide.size == (1024, 512)  # shrunk for the model
    assert small.size == (300, 200)  # never enlarged
    red = wide.convert("RGB").getpixel((100, 256))
    assert red[0] > 200 and red[2] < 60  # the photo itself, not a stand-in


def test_a_local_text_model_is_told_instead(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=False)
    entry_id = seed(client, "At the harbour", SMALL)

    content = user_turns(reply_to(client, model, entry_id))[-1]

    assert content == (
        "At the harbour\n\n[The writer attached 1 photo to this entry; you can't see it.]"
    )


def test_a_hosted_api_gets_no_images_unless_asked(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True, hosted=True)  # no /props to say so
    entry_id = seed(client, "Private photo", SMALL)

    content = user_turns(reply_to(client, model, entry_id))[-1]

    assert images_in(content) == [] and "you can't see it" in content


def test_a_hosted_api_gets_images_when_enabled(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(
        vision=False, hosted=True, env={"LLAMORA_LLM__VISION__ENABLED": "true"}
    )
    entry_id = seed(client, "", SMALL)

    content = user_turns(reply_to(client, model, entry_id))[-1]

    assert texts_in(content) == [] and len(images_in(content)) == 1


def test_disabled_means_never(diary: Callable[..., ApiClient], model: FakeLLM) -> None:
    client = diary(vision=True, env={"LLAMORA_LLM__VISION__ENABLED": "false"})
    entry_id = seed(client, "Not for the model", SMALL)

    content = user_turns(reply_to(client, model, entry_id))[-1]

    assert isinstance(content, str) and "you can't see it" in content


def test_the_entry_first_then_earlier_images_up_to_the_limit(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True, env={"LLAMORA_LLM__VISION__MAX_IMAGES": "2"})
    seed(client, "Morning", SMALL, SMALL)
    entry_id = seed(client, "Evening", WIDE)

    morning, evening = user_turns(reply_to(client, model, entry_id))

    assert len(images_in(evening)) == 1  # the entry being answered
    assert len(images_in(morning)) == 1  # the newest earlier image fills the rest
    assert texts_in(morning)[-1] == "[1 more photo attached to this entry, not shown.]"


# -- the prompt budget (the fake model has an 8192-token context: with the
#    safety margin and room for the reply, about 5,300 prompt tokens) --------


def test_images_that_do_not_fit_give_way_oldest_first(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True, env={"LLAMORA_LLM__VISION__TOKENS_PER_IMAGE": "2000"})
    seed(client, "Morning", SMALL, SMALL)  # 3 images selected; 2 fit
    entry_id = seed(client, "Evening", WIDE)

    morning, evening = user_turns(reply_to(client, model, entry_id))

    assert len(images_in(evening)) == 1  # the entry's own image stays
    assert len(images_in(morning)) == 1  # the newer earlier one; the oldest went
    assert texts_in(morning)[-1] == "[1 more photo attached to this entry, not shown.]"


def test_an_image_too_big_for_the_context_is_told_instead(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True, env={"LLAMORA_LLM__VISION__TOKENS_PER_IMAGE": "6000"})
    entry_id = seed(client, "One huge photo", SMALL)

    content = user_turns(reply_to(client, model, entry_id))[-1]

    assert content == (
        "One huge photo\n\n[The writer attached 1 photo to this entry; you can't see it.]"
    )


def test_by_default_a_full_entry_is_seen_whole(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True)
    entry_id = seed(client, "Eight photos", *[SMALL] * 8)

    content = user_turns(reply_to(client, model, entry_id))[-1]

    assert len(images_in(content)) == 8  # max_images follows the per-entry limit


# -- tag suggestions ("Add traces") -------------------------------------------


def suggest(
    client: ApiClient, model: FakeLLM, entry_id: str
) -> tuple[str, dict | None]:
    """Open an entry's tag suggestions; return the HTML and the model request."""
    before = len(model.chat_requests(structured=True))
    resp = client.http.get(f"/t/entry/{entry_id}/suggestions", headers=client.headers)
    resp.raise_for_status()
    structured = model.chat_requests(structured=True)
    return resp.text, (structured[-1] if len(structured) > before else None)


def test_tag_suggestions_see_the_images(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True)
    entry_id = seed(client, "At the harbour", SMALL)

    html, request = suggest(client, model, entry_id)

    assert request is not None
    content = user_turns(request)[-1]
    assert texts_in(content) == ["At the harbour"]
    assert len(images_in(content)) == 1
    assert (
        "let what they show inform the emoji and tags"
        in request["messages"][0]["content"]
    )
    assert 'data-tag="morning"' in html  # the fake model's tags reach the popover


def test_an_entry_of_only_images_gets_tag_suggestions(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True)
    entry_id = seed(client, "", SMALL)

    html, request = suggest(client, model, entry_id)

    assert request is not None and len(images_in(user_turns(request)[-1])) == 1
    assert 'data-tag="coffee"' in html


def test_without_vision_tag_suggestions_are_text_only(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=False)
    entry_id = seed(client, "At the harbour", SMALL)
    only_images = seed(client, "", SMALL)

    _, request = suggest(client, model, entry_id)
    assert request is not None and user_turns(request)[-1] == "At the harbour"

    _, request = suggest(client, model, only_images)
    assert request is None  # nothing to ask the model about


def test_changing_the_images_refreshes_suggestions(
    diary: Callable[..., ApiClient], model: FakeLLM
) -> None:
    client = diary(vision=True)
    entry_id = seed(client, "At the harbour", SMALL)
    suggest(client, model, entry_id)
    _, again = suggest(client, model, entry_id)
    assert again is None  # cached

    new_image = client.upload_image_id(WIDE)
    client.update_entry(entry_id, "At the harbour", image_ids=[new_image])
    _, refreshed = suggest(client, model, entry_id)

    assert refreshed is not None
    assert images_in(user_turns(refreshed)[-1])[0].size == (1024, 512)
