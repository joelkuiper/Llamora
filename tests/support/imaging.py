"""Test images, made in memory with Pillow."""

from __future__ import annotations

import io
import os
import struct
import zlib

from PIL import ExifTags, Image, ImageCms

CAMERA = "LlamoraTestCam"


def encode(image: Image.Image, fmt: str, **params) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **params)
    return buffer.getvalue()


def halves(size=(300, 200), left="red", right="blue", mode="RGB") -> Image.Image:
    """Left half one colour, right half another: shows how it was rotated."""
    image = Image.new(mode, size, left)
    image.paste(right, (size[0] // 2, 0, size[0], size[1]))
    return image


def jpeg_with_gps(orientation: int = 6) -> bytes:
    """A landscape JPEG with GPS, camera make and an EXIF orientation."""
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = CAMERA
    exif[ExifTags.Base.Orientation] = orientation
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (52.0, 22.0, 13.0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (4.0, 53.0, 27.0)
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return encode(halves(), "JPEG", exif=exif, icc_profile=srgb, quality=90)


def png_with_alpha(size=(120, 80)) -> bytes:
    image = Image.new("RGBA", size, (255, 0, 0, 255))
    image.paste((0, 0, 255, 0), (0, 0, size[0] // 2, size[1]))
    return encode(image, "PNG")


def opaque_rgba_png(size=(120, 80)) -> bytes:
    return encode(Image.new("RGBA", size, (10, 200, 30, 255)), "PNG")


def animated_gif(frames=("red", "green", "blue"), size=(64, 48)) -> bytes:
    images = [Image.new("RGB", size, colour) for colour in frames]
    return encode(
        images[0], "GIF", save_all=True, append_images=images[1:], duration=100
    )


def png_header_only(width: int, height: int) -> bytes:
    """A PNG that claims huge dimensions but carries almost no data."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit grey
    idat = zlib.compress(b"\x00" * 16)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def noise(size=(256, 256)) -> Image.Image:
    """Random pixels: compresses badly, so encoded files span several chunks."""
    return Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
