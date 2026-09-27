"""Uploads become clean, metadata-free variants (images.processing)."""

from __future__ import annotations

import pytest
from PIL import Image

from imaging import (
    CAMERA,
    animated_gif,
    decode,
    encode,
    halves,
    jpeg_with_gps,
    opaque_rgba_png,
    png_header_only,
    png_with_alpha,
)
from llamora.app.services.images import VARIANTS, ImageRejected
from llamora.app.services.images.processing import process

SIZES = {"thumb": 48, "display": 128, "full": 256}


def run(data: bytes, **kwargs):
    return process(data, **{"sizes": SIZES, **kwargs})


def test_every_variant_is_webp_within_its_size() -> None:
    result = run(encode(halves((1000, 500)), "JPEG"))
    assert set(result.variants) == set(VARIANTS)
    for name, variant in result.variants.items():
        image = decode(variant.data)
        assert image.format == "WEBP" and variant.mime == "image/webp"
        assert (image.width, image.height) == (variant.width, variant.height)
        assert max(image.size) == SIZES[name]
        assert image.width == 2 * image.height  # aspect ratio kept


def test_small_images_are_never_upscaled() -> None:
    result = run(encode(halves((100, 60)), "PNG"))
    assert (result.variants["full"].width, result.variants["full"].height) == (100, 60)
    assert (result.variants["display"].width, result.variants["display"].height) == (
        100,
        60,
    )
    assert max(result.variants["thumb"].width, result.variants["thumb"].height) == 48


@pytest.mark.parametrize("size", [(5000, 20), (20, 5000)])
def test_extreme_aspect_ratios_keep_at_least_a_pixel(size) -> None:
    result = run(encode(Image.new("RGB", size, "grey"), "PNG"))
    thumb = result.variants["thumb"]
    assert max(thumb.width, thumb.height) == 48
    assert min(thumb.width, thumb.height) >= 1


def test_exif_gps_camera_and_icc_are_stripped() -> None:
    source = jpeg_with_gps()
    assert CAMERA.encode() in source  # the fixture really carries metadata

    for variant in run(source).variants.values():
        image = decode(variant.data)
        assert not image.getexif()
        assert not {"exif", "icc_profile", "xmp"} & set(image.info)
        assert CAMERA.encode() not in variant.data
        assert b"Exif" not in variant.data


def test_orientation_is_applied() -> None:
    # Stored landscape (red left, blue right), orientation 6: shown rotated
    # 90° clockwise, so portrait with red on top.
    image = decode(run(jpeg_with_gps(orientation=6)).variants["full"].data).convert(
        "RGB"
    )
    assert image.width < image.height
    top = image.getpixel((image.width // 2, image.height // 4))
    bottom = image.getpixel((image.width // 2, 3 * image.height // 4))
    assert top[0] > 200 and top[2] < 60, top
    assert bottom[2] > 200 and bottom[0] < 60, bottom


def test_real_transparency_is_kept_losslessly() -> None:
    image = decode(run(png_with_alpha()).variants["full"].data)
    assert image.mode == "RGBA"
    assert image.getpixel((5, 5))[3] == 0  # transparent half
    assert image.getpixel((image.width - 5, 5)) == (255, 0, 0, 255)  # exact


def test_opaque_alpha_channel_is_dropped() -> None:
    image = decode(run(opaque_rgba_png()).variants["full"].data)
    assert image.mode == "RGB"


def test_palette_image_with_transparency() -> None:
    palette = Image.new("P", (40, 40), 0)
    palette.putpalette([255, 0, 0, 0, 255, 0])
    palette.paste(1, (0, 0, 20, 40))
    data = encode(palette, "PNG", transparency=0)
    image = decode(run(data).variants["full"].data)
    assert image.mode == "RGBA"
    assert image.getpixel((30, 5))[3] == 0


def test_animated_gif_keeps_its_first_frame() -> None:
    result = run(animated_gif(("red", "green", "blue")))
    image = decode(result.variants["full"].data)
    assert getattr(image, "n_frames", 1) == 1
    red, green, blue = image.convert("RGB").getpixel((10, 10))
    assert red > 200 and green < 60 and blue < 60


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF"])
def test_allowed_formats(fmt: str) -> None:
    result = run(encode(halves((80, 40)), fmt))
    assert result.source_format == fmt


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"just some text, not an image", ImageRejected.UNSUPPORTED_FORMAT),
        (b"<!doctype html><script>alert(1)</script>", ImageRejected.UNSUPPORTED_FORMAT),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
            ImageRejected.UNSUPPORTED_FORMAT,
        ),
        (b"", ImageRejected.CORRUPT),
    ],
    ids=["text", "html", "svg", "empty"],
)
def test_non_images_are_rejected(data: bytes, reason: str) -> None:
    with pytest.raises(ImageRejected) as excinfo:
        run(data)
    assert excinfo.value.reason == reason


@pytest.mark.parametrize("fmt", ["BMP", "TIFF", "ICO"])
def test_other_image_formats_are_rejected(fmt: str) -> None:
    with pytest.raises(ImageRejected) as excinfo:
        run(encode(halves((32, 32)), fmt))
    assert excinfo.value.reason == ImageRejected.UNSUPPORTED_FORMAT


def test_truncated_image_is_rejected() -> None:
    data = encode(halves((400, 300)), "JPEG")
    with pytest.raises(ImageRejected) as excinfo:
        run(data[: len(data) // 2])
    assert excinfo.value.reason == ImageRejected.CORRUPT


@pytest.mark.parametrize(
    "size",
    [(10_000, 6_000), (100_000, 100_000)],  # over max_pixels; over Pillow's own guard
    ids=["over-limit", "absurd"],
)
def test_pixel_bombs_are_rejected_before_decoding(size) -> None:
    with pytest.raises(ImageRejected) as excinfo:
        run(png_header_only(*size))
    assert excinfo.value.reason == ImageRejected.TOO_MANY_PIXELS


def test_max_pixels_is_configurable() -> None:
    data = encode(halves((200, 200)), "PNG")
    with pytest.raises(ImageRejected) as excinfo:
        run(data, max_pixels=10_000)
    assert excinfo.value.reason == ImageRejected.TOO_MANY_PIXELS


def test_max_bytes() -> None:
    data = encode(halves((200, 200)), "PNG")
    with pytest.raises(ImageRejected) as excinfo:
        run(data, max_bytes=len(data) - 1)
    assert excinfo.value.reason == ImageRejected.TOO_LARGE


def test_polyglot_payload_does_not_survive() -> None:
    payload = b"<html><script>alert(document.cookie)</script></html>"
    data = encode(halves((120, 80)), "JPEG") + payload
    for variant in run(data).variants.values():
        assert b"<script" not in variant.data and b"<html" not in variant.data


def test_heic_from_an_iphone_is_accepted_and_cleaned() -> None:
    from PIL import ExifTags, ImageCms

    from imaging import heic

    exif = Image.Exif()
    exif[ExifTags.Base.Make] = CAMERA
    p3ish = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    source = heic(halves((600, 400)), exif=exif, icc_profile=p3ish)
    assert source[4:12] == b"ftypheic" and CAMERA.encode() in source

    result = run(source)

    assert result.source_format == "HEIF"
    full = decode(result.variants["full"].data)
    assert full.format == "WEBP" and (full.width, full.height) == (256, 171)
    assert not full.getexif() and "icc_profile" not in full.info
    assert all(CAMERA.encode() not in v.data for v in result.variants.values())
    red = full.convert("RGB").getpixel((20, 80))
    assert red[0] > 200 and red[2] < 60  # colours survive decoding
