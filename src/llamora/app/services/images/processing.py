"""Turn an upload into clean, metadata-free image variants (Pillow only).

The upload is decoded, never trusted: the format is detected from the bytes,
the pixel count is checked before any pixel data is decoded, and every
variant is re-encoded from pixels alone, so EXIF (GPS, camera serials), XMP,
ICC profiles, comments and anything smuggled after the image data are gone.

CPU-bound and synchronous: callers run it in a worker thread.
"""

from __future__ import annotations

import io
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from logging import getLogger

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from llamora.app.services.images import VARIANTS, ImageRejected, Variant

logger = getLogger(__name__)

# Pillow format names. iPhone and many cameras write MPO: a JPEG with extra
# (depth/preview) pictures appended; the first picture is the photo.
ALLOWED_FORMATS = frozenset({"JPEG", "MPO", "PNG", "WEBP", "GIF"})
OUTPUT_MIME = "image/webp"

_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))


@dataclass(frozen=True, slots=True)
class EncodedVariant:
    mime: str
    width: int
    height: int
    data: bytes


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    source_format: str
    variants: dict[Variant, EncodedVariant]


def process(
    data: bytes,
    *,
    sizes: Mapping[str, int],
    quality: int = 85,
    max_pixels: int = 50_000_000,
    max_bytes: int | None = None,
) -> ProcessedImage:
    """Validate an upload and encode every variant.

    ``sizes`` maps each variant to its maximum long edge; images are never
    upscaled. Raises :class:`ImageRejected` for anything that is not a
    supported, sane image.
    """

    if max_bytes is not None and len(data) > max_bytes:
        raise ImageRejected(ImageRejected.TOO_LARGE)
    if not data:
        raise ImageRejected(ImageRejected.CORRUPT, "empty upload")

    with warnings.catch_warnings():
        # Pillow's own bomb guard warns between MAX_IMAGE_PIXELS and twice
        # that; treat it as fatal. Our max_pixels check normally fires first.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = _open(data, max_pixels=max_pixels)
            image = _normalize(image)
        except ImageRejected:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ImageRejected(ImageRejected.TOO_MANY_PIXELS) from None
        except (OSError, ValueError, SyntaxError, EOFError) as exc:
            raise ImageRejected(ImageRejected.CORRUPT, str(exc)) from None

    variants: dict[Variant, EncodedVariant] = {}
    # Largest first; each smaller variant is resized from the previous one.
    current = image
    for variant in sorted(VARIANTS, key=lambda v: -int(sizes[v])):
        edge = int(sizes[variant])
        if max(current.size) > edge:
            current = current.copy()
            current.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        variants[variant] = _encode(current, quality=quality)
    return ProcessedImage(
        source_format=str(image.info.get("source_format", "")),
        variants={v: variants[v] for v in VARIANTS},
    )


def _open(data: bytes, *, max_pixels: int) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError:
        raise ImageRejected(ImageRejected.UNSUPPORTED_FORMAT) from None
    fmt = image.format or ""
    if fmt not in ALLOWED_FORMATS:
        raise ImageRejected(ImageRejected.UNSUPPORTED_FORMAT, fmt)
    width, height = image.size
    # Only the header has been read so far: reject before decoding pixels.
    if width <= 0 or height <= 0:
        raise ImageRejected(ImageRejected.CORRUPT, "no pixels")
    if width * height > max_pixels:
        raise ImageRejected(ImageRejected.TOO_MANY_PIXELS)
    image.seek(0)  # animations: the first frame only
    image.load()
    image.info["source_format"] = "JPEG" if fmt == "MPO" else fmt
    return image


def _normalize(image: Image.Image) -> Image.Image:
    """Upright, sRGB, RGB or RGBA (only when the alpha is actually used)."""

    source_format = image.info.get("source_format")
    icc = image.info.get("icc_profile")
    image = ImageOps.exif_transpose(image)

    has_alpha = image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        "transparency" in image.info
    )
    target = "RGBA" if has_alpha else "RGB"
    if icc:
        image = _to_srgb(image, icc, target)
    if image.mode != target:
        image = image.convert(target)
    if target == "RGBA" and image.getchannel("A").getextrema() == (255, 255):
        image = image.convert("RGB")  # an opaque image stored with alpha

    # Pixels only: nothing from the source's info survives.
    clean = image.copy()
    clean.info = {"source_format": source_format}
    return clean


def _to_srgb(image: Image.Image, icc: bytes, target: str) -> Image.Image:
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if image.mode not in ("RGB", "RGBA", "CMYK"):
            image = image.convert(target)
        output = target if image.mode != "CMYK" else "RGB"
        converted = ImageCms.profileToProfile(image, source, _SRGB, outputMode=output)
        return converted if converted is not None else image
    except (ImageCms.PyCMSError, OSError, ValueError):
        logger.debug("Could not apply embedded ICC profile; dropping it")
        return image


def _encode(image: Image.Image, *, quality: int) -> EncodedVariant:
    buffer = io.BytesIO()
    if image.mode == "RGBA":
        image.save(buffer, "WEBP", lossless=True, method=4)
    else:
        image.save(buffer, "WEBP", quality=int(quality), method=4)
    return EncodedVariant(
        mime=OUTPUT_MIME,
        width=image.width,
        height=image.height,
        data=buffer.getvalue(),
    )
