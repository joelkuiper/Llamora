"""Image attachments: upload, view and delete (see doc/specs/images.md)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from quart import Blueprint, Response, jsonify, render_template, request

from llamora.app.routes.entries import (
    _set_cache_invalidation_header,
    delete_entry_response,
)
from llamora.app.routes.helpers import abort_http, require_encryption_context
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.cache_registry import MUTATION_ENTRY_CHANGED
from llamora.app.services.container import get_services
from llamora.app.services.images import VARIANTS, ImageDecryptError, ImageRejected
from llamora.app.services.images.service import TOO_MANY_PENDING

images_bp = Blueprint("images", __name__)

logger = logging.getLogger(__name__)

# Rejection reason -> (status, message for people).
_REJECTIONS: dict[str, tuple[int, str]] = {
    ImageRejected.TOO_LARGE: (413, "Too large"),
    ImageRejected.UNSUPPORTED_FORMAT: (415, "Not an image"),
    ImageRejected.TOO_MANY_PIXELS: (400, "Too many pixels"),
    ImageRejected.CORRUPT: (400, "Unreadable image"),
    TOO_MANY_PENDING: (429, "Too many images waiting"),
}


def _error(status: int, reason: str, message: str) -> Response:
    response = jsonify({"error": reason, "message": message})
    response.status_code = status
    return response


@images_bp.post("/i")
@login_required
async def upload_image():
    """Store one image as pending; it is attached when an entry is sent."""

    _, _user, ctx = await require_encryption_context()
    files = await request.files
    upload = files.get("image")
    if upload is None:
        return _error(400, "missing", "No image")
    data = upload.read()
    try:
        record = await get_services().images.upload(ctx, upload.filename, data)
    except ImageRejected as exc:
        logger.info("Rejected upload (%s): %d bytes", exc.reason, len(data))
        status, message = _REJECTIONS.get(exc.reason, (400, "Unreadable image"))
        return _error(status, exc.reason, message)
    thumb = record.meta.variants["thumb"]
    response = jsonify(
        {"id": record.id, "thumb": {"width": thumb.width, "height": thumb.height}}
    )
    response.status_code = 201
    return response


def _cache_headers(response: Response, etag: str) -> None:
    # Revalidate every time (nothing decrypted lingers as fresh in the browser
    # cache); a matching ETag is answered before any decryption.
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Vary"] = "Cookie"


@images_bp.get("/i/<image_id>/<variant>")
@login_required
async def image_variant(image_id: str, variant: str):
    if variant not in VARIANTS:
        abort_http(404, "Not found")
    _, user, ctx = await require_encryption_context()
    images = get_services().images

    # Ownership first: not even a 304 for someone else's image.
    if not await images.exists(str(user["id"]), image_id):
        abort_http(404, "Not found")
    etag = f"{image_id}.{variant}"
    if etag in request.if_none_match:
        response = Response("", status=304)
        _cache_headers(response, etag)
        return response

    try:
        opened = await images.open_variant(ctx, image_id, variant)
    except ImageDecryptError:
        logger.warning(
            "Image %s (%s) failed to decrypt", image_id, variant, exc_info=True
        )
        abort_http(404, "Not found")
    if opened is None:
        abort_http(404, "Not found")
    meta, stream = opened

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream:
                yield chunk
        except ImageDecryptError:
            # Headers are sent: cut the response short rather than finish it.
            logger.error("Image %s (%s) failed mid-stream", image_id, variant)
            raise

    response = Response(body(), status=200, mimetype=meta.mime)
    response.headers["Content-Length"] = str(meta.bytes)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Content-Disposition"] = "inline"
    _cache_headers(response, etag)
    return response


@images_bp.delete("/i/<image_id>")
@login_required
async def delete_image(image_id: str):
    """Delete a pending or attached image.

    Removing the last image of an entry without text would leave it empty:
    that is refused (409) unless ``with_entry=1`` asks to delete the entry.
    """

    _, user, ctx = await require_encryption_context()
    user_id = str(user["id"])
    services = get_services()
    db = services.db
    row = await db.images.get(user_id, image_id)
    if row is None:
        abort_http(404, "Not found")

    entry_id = row.entry_id
    if entry_id is None:
        await services.images.delete(ctx, image_id)
        return Response("", status=204)

    entries = await db.entries.get_entries_by_ids(ctx, [entry_id])
    entry = entries[0] if entries else None
    if entry is not None and not str(entry.get("text") or "").strip():
        if await db.images.count_for_entry(user_id, entry_id) <= 1:
            if request.args.get("with_entry") != "1":
                return _error(
                    409,
                    "last_image",
                    "This entry has no text; removing its only image deletes it.",
                )
            return await delete_entry_response(user_id, entry_id)

    await services.images.delete(ctx, image_id)

    payload = {
        "id": entry_id,
        "role": "user",
        "text": str(entry.get("text") or "") if entry else "",
    }
    await services.images.attach_refs(ctx, [payload])
    html = await render_template(
        "components/entries/entry_images.html", entry=payload, oob=True
    )
    response = Response(html, status=200, mimetype="text/html")
    tags = await db.tags.get_tags_for_entry(ctx, entry_id)
    day = str(entry.get("created_date") or "") if entry else ""
    _set_cache_invalidation_header(
        response,
        mutation=MUTATION_ENTRY_CHANGED,
        reason="entry.image_removed",
        created_dates=(day,) if day else (),
        tag_hashes=tuple(str(t.get("hash") or "") for t in tags if t.get("hash")),
    )
    return response
