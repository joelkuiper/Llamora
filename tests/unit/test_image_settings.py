"""Image settings are checked at startup (config_validation._validate_images)."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path

import pytest

from llamora.app.services.config_validation import _validate_images
from llamora.settings import settings


@pytest.fixture
def images_settings(tmp_path: Path) -> Iterator[dict]:
    """A valid IMAGES block to break one piece at a time (restored after)."""
    original = copy.deepcopy(settings.get("IMAGES").to_dict())
    block = {**copy.deepcopy(original), "path": str(tmp_path / "images")}
    settings.set("IMAGES", block)
    yield block
    settings.set("IMAGES", original)


def errors(block: dict) -> list[str]:
    settings.set("IMAGES", block)
    return list(_validate_images())


def test_defaults_are_valid(images_settings: dict) -> None:
    assert errors(images_settings) == []


def test_path_that_is_a_file(images_settings: dict, tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    assert errors({**images_settings, "path": str(tmp_path / "file")}) == [
        f"IMAGES.path ({tmp_path / 'file'}) exists but is not a directory."
    ]


def test_path_that_cannot_be_created(images_settings: dict, tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        found = errors({**images_settings, "path": str(locked / "images")})
    finally:
        locked.chmod(0o700)
    assert found == [f"IMAGES.path ({locked / 'images'}) is not writable."]


def test_path_whose_parents_do_not_exist_yet(
    images_settings: dict, tmp_path: Path
) -> None:
    assert errors({**images_settings, "path": str(tmp_path / "a" / "b" / "c")}) == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_upload_bytes", 0),
        ("max_per_entry", 0),
        ("max_per_entry", 101),
        ("quality", 0),
        ("quality", 101),
        ("processing_concurrency", 0),
        ("pending_ttl", 5),
        ("sweep_interval", "often"),
    ],
)
def test_limits_out_of_range(images_settings: dict, name: str, value) -> None:
    found = errors({**images_settings, name: value})
    assert len(found) == 1 and found[0].startswith(f"IMAGES.{name} must be")


@pytest.mark.parametrize(
    ("sizes", "message"),
    [
        ({"thumb": 480, "display": 2048, "full": 1024}, "must grow"),
        ({"thumb": 0, "display": 2048, "full": 4096}, "IMAGES.sizes.thumb"),
        ({"thumb": 480, "display": 2048, "full": 20000}, "IMAGES.sizes.full"),
    ],
)
def test_sizes(images_settings: dict, sizes: dict, message: str) -> None:
    found = errors({**images_settings, "sizes": sizes})
    assert len(found) == 1 and message in found[0]
