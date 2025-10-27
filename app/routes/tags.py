from quart import Blueprint, request, abort, render_template
from app.services.container import get_services
from app.services.auth_helpers import login_required, get_current_user, get_dek
from config import MAX_TAG_LENGTH

tags_bp = Blueprint("tags", __name__)


def _db():
    return get_services().db


@tags_bp.delete("/t/<msg_id>/<tag_hash>")
@login_required
async def remove_tag(msg_id: str, tag_hash: str):
    user = await get_current_user()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError:
        abort(400, description="invalid tag hash")
    await _db().tags.unlink_tag_message(user["id"], tag_hash_bytes, msg_id)
    return "<span class='chip-tombstone'></span>"


@tags_bp.post("/t/<msg_id>")
@login_required
async def add_tag(msg_id: str):
    user = await get_current_user()
    form = await request.form
    tag = (form.get("tag") or "").strip()
    if tag and not tag.startswith("#"):
        tag = f"#{tag}"
    if not tag:
        abort(400, description="empty tag")
    if len(tag) > MAX_TAG_LENGTH:
        abort(400, description="tag too long")
    dek = get_dek()
    if not await _db().messages.message_exists(user["id"], msg_id):
        abort(404, description="message not found")
    tag_hash = await _db().tags.resolve_or_create_tag(user["id"], tag, dek)
    await _db().tags.xref_tag_message(user["id"], tag_hash, msg_id)
    html = await render_template(
        "partials/tag_chip.html", keyword=tag, tag_hash=tag_hash.hex(), msg_id=msg_id
    )
    return html


@tags_bp.get("/t/suggestions/<msg_id>")
@login_required
async def get_tag_suggestions(msg_id: str):
    user = await get_current_user()
    dek = get_dek()
    messages = await _db().messages.get_messages_by_ids(user["id"], [msg_id], dek)
    if not messages:
        abort(404, description="message not found")
    meta = messages[0].get("meta", {})
    keywords = meta.get("keywords") or []
    existing = await _db().tags.get_tags_for_message(user["id"], msg_id, dek)
    existing_names = {t["name"] for t in existing}

    meta_suggestions: set[str] = set()
    for kw in keywords:
        kw = (kw or "").strip()
        if kw and not kw.startswith("#"):
            kw = f"#{kw}"
        kw = kw[:MAX_TAG_LENGTH]
        if kw:
            meta_suggestions.add(kw)

    frecent_tags = await _db().tags.get_tag_frecency(user["id"], 3, 0.0001, dek)
    frecent_suggestions = {
        t["name"] if t["name"].startswith("#") else f"#{t['name']}"
        for t in frecent_tags
    }

    combined = meta_suggestions | frecent_suggestions
    combined = [name for name in combined if name not in existing_names]

    html = await render_template(
        "partials/tag_suggestions.html", suggestions=combined, msg_id=msg_id
    )
    return html
