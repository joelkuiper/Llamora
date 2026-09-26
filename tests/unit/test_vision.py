"""Deciding and preparing what a vision model sees (llm.vision, the client,
images.processing.encode_for_model)."""

from __future__ import annotations

import asyncio
import copy
import logging
from types import SimpleNamespace

import pytest

from imaging import decode, encode, halves
from llamora.app.services.config_validation import _validate_llm_vision
from llamora.app.services.images.processing import encode_for_model
from llamora.llm.client import LLMClient
from llamora.llm.entry_template import IMAGE_REF
from llamora.llm.vision import (
    VisionConfig,
    parse_vision_mode,
    props_have_vision,
    vision_enabled,
)
from llamora.settings import settings

LLAMA_VISION = {"n_ctx": 8192, "modalities": {"vision": True, "audio": False}}
LLAMA_TEXT = {"n_ctx": 8192, "modalities": {"vision": False, "audio": False}}


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        ("auto", "auto"),
        (None, "auto"),
        ("", "auto"),
        (True, "on"),
        ("true", "on"),
        ("TRUE", "on"),
        (False, "off"),
        ("false", "off"),
        ("sometimes", None),
    ],
)
def test_parse_vision_mode(value, mode) -> None:
    assert parse_vision_mode(value) == mode


@pytest.mark.parametrize(
    ("mode", "props", "sends"),
    [
        ("auto", LLAMA_VISION, True),  # local llama.cpp with --mmproj
        ("auto", LLAMA_TEXT, False),  # local llama.cpp, text model
        ("auto", {"n_ctx": 8192}, False),  # older llama.cpp: no modalities
        ("auto", None, False),  # hosted API: no /props, so never unasked
        ("on", None, True),  # hosted vision model, explicitly enabled
        ("on", LLAMA_TEXT, True),  # explicit wins
        ("off", LLAMA_VISION, False),
    ],
)
def test_when_images_are_sent(mode, props, sends: bool) -> None:
    assert vision_enabled(mode, props) is sends


def test_props_have_vision_is_strict_about_shape() -> None:
    assert not props_have_vision({"modalities": True})
    assert not props_have_vision({"modalities": ["vision"]})


def test_defaults_from_settings() -> None:
    assert VisionConfig.from_settings(settings) == VisionConfig(
        mode="auto", max_images=8, max_edge=1024, quality=85
    )


def test_max_images_follows_the_entry_limit_unless_set(vision_settings) -> None:
    images = settings.get("IMAGES").to_dict()
    try:
        settings.set("IMAGES", {**images, "max_per_entry": 5})
        assert VisionConfig.from_settings(settings).max_images == 5
        settings.set(
            "LLM.vision", {**settings.get("LLM.vision").to_dict(), "max_images": 2}
        )
        assert VisionConfig.from_settings(settings).max_images == 2
    finally:
        settings.set("IMAGES", images)


def test_client_policy_follows_props_and_setting() -> None:
    stub = SimpleNamespace(
        vision=VisionConfig(mode="auto", max_images=2),
        upstream=SimpleNamespace(upstream_props=LLAMA_VISION),
    )
    policy = LLMClient.image_policy.fget(stub)
    assert policy.send and policy.max_images == 2
    stub.upstream.upstream_props = None
    assert not LLMClient.image_policy.fget(stub).send


# -- resolving references ------------------------------------------------------


def resolve(messages, resolver):
    stub = SimpleNamespace(logger=logging.getLogger("test"))
    return asyncio.run(LLMClient._resolve_images(stub, messages, resolver))


def ref(image_id: str) -> dict:
    return {"type": IMAGE_REF, "image_id": image_id}


def test_references_become_images_and_failures_a_note() -> None:
    calls: list[str] = []

    async def resolver(image_id: str) -> str | None:
        calls.append(image_id)
        if image_id == "boom":
            raise RuntimeError("disk on fire")
        return f"data:image/jpeg;base64,{image_id}" if image_id != "gone" else None

    messages = [
        {"role": "system", "content": "Be kind."},
        {
            "role": "user",
            "content": [{"type": "text", "text": "Look"}, ref("a"), ref("gone")],
        },
        {"role": "user", "content": [ref("a"), ref("boom")]},
    ]

    system, first, second = resolve(messages, resolver)

    assert system == messages[0]
    assert first["content"] == [
        {"type": "text", "text": "Look"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,a"}},
        {
            "type": "text",
            "text": "[The writer attached 1 photo to this entry; you can't see it.]",
        },
    ]
    assert second["content"][0]["type"] == "image_url"
    assert second["content"][-1]["text"].endswith("you can't see it.]")
    assert calls.count("a") == 1  # each image prepared once per reply


def test_without_a_resolver_every_reference_is_a_note() -> None:
    (message,) = resolve([{"role": "user", "content": [ref("a"), ref("b")]}], None)
    assert message["content"] == [
        {
            "type": "text",
            "text": "[The writer attached 2 photos to this entry; you can't see them.]",
        }
    ]


# -- encoding for the model -----------------------------------------------------


def test_images_are_shrunk_to_jpeg() -> None:
    webp = encode(halves((3000, 1500)), "WEBP")
    image = decode(encode_for_model(webp, max_edge=1024))
    assert image.format == "JPEG" and image.size == (1024, 512)


def test_small_images_are_not_enlarged() -> None:
    image = decode(encode_for_model(encode(halves((300, 200)), "WEBP"), max_edge=1024))
    assert image.size == (300, 200)


def test_transparency_is_flattened_onto_white() -> None:
    from PIL import Image

    clear = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    image = decode(encode_for_model(encode(clear, "WEBP", lossless=True), max_edge=64))
    assert image.mode == "RGB"
    assert min(image.getpixel((20, 20))) > 245


# -- settings ------------------------------------------------------------------------


@pytest.fixture
def vision_settings():
    original = copy.deepcopy(settings.get("LLM.vision").to_dict())
    yield
    settings.set("LLM.vision", original)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({}, None),
        ({"enabled": True}, None),
        ({"enabled": "false"}, None),
        ({"enabled": "maybe"}, "LLM.vision.enabled"),
        ({"max_images": 99}, "LLM.vision.max_images"),
        ({"max_edge": 10}, "LLM.vision.max_edge"),
        ({"quality": 0}, "LLM.vision.quality"),
    ],
)
def test_vision_settings_are_validated(vision_settings, override, message) -> None:
    settings.set("LLM.vision", {**settings.get("LLM.vision").to_dict(), **override})
    errors = list(_validate_llm_vision())
    if message is None:
        assert errors == []
    else:
        assert len(errors) == 1 and errors[0].startswith(message)
