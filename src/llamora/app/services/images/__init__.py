"""Image attachments: encrypted at rest, linked to entries.

Each stored image has three variants (thumb, display, full), all re-encoded
without metadata; the original upload is never kept.

- ``processing``: validate an upload and encode the variants (Pillow).
- ``format``: the encrypted file format (per-image key, secretstream chunks).
- ``blob_store``: the files on disk under ``IMAGES.path``.
- ``service``: ``ImageService``, tying these to the rows in ``db.images``
  (upload, read, delete, sweep).

Routes are in ``app/routes/images.py``; linking images to entries happens in
the entries repository (``image_ids`` on append/update).
"""

from __future__ import annotations

from typing import Literal

Variant = Literal["thumb", "display", "full"]
VARIANTS: tuple[Variant, ...] = ("thumb", "display", "full")


class ImageRejected(ValueError):
    """An upload that cannot be stored as an image."""

    TOO_LARGE = "too_large"
    UNSUPPORTED_FORMAT = "unsupported_format"
    TOO_MANY_PIXELS = "too_many_pixels"
    CORRUPT = "corrupt"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class ImageDecryptError(ValueError):
    """An encrypted image file that is tampered with, truncated or misplaced."""
