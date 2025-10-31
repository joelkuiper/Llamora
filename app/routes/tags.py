from quart import Blueprint, request, abort, render_template, jsonify
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
    if user is None:
        abort(401)
        raise AssertionError("unreachable")
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc
    await _db().tags.unlink_tag_message(user["id"], tag_hash_bytes, msg_id)
    return "<span class='chip-tombstone'></span>"


@tags_bp.post("/t/<msg_id>")
@login_required
async def add_tag(msg_id: str):
    user = await get_current_user()
    if user is None:
        abort(401)
        raise AssertionError("unreachable")
    form = await request.form
    tag = (form.get("tag") or "").strip()
    if tag and not tag.startswith("#"):
        tag = f"#{tag}"
    if not tag:
        abort(400, description="empty tag")
    if len(tag) > MAX_TAG_LENGTH:
        abort(400, description="tag too long")
    dek = get_dek()
    if dek is None:
        abort(401, description="Missing encryption key")
        raise AssertionError("unreachable")
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
    if user is None:
        abort(401)
        raise AssertionError("unreachable")
    dek = get_dek()
    if dek is None:
        abort(401, description="Missing encryption key")
        raise AssertionError("unreachable")
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


@tags_bp.get("/tags/autocomplete")
@login_required
async def autocomplete_tags():
    user = await get_current_user()
    if user is None:
        abort(401)
        raise AssertionError("unreachable")

    dek = get_dek()
    if dek is None:
        abort(401, description="Missing encryption key")
        raise AssertionError("unreachable")

    msg_id = (request.args.get("msg_id") or "").strip()
    if not msg_id:
        abort(400, description="message id required")
        raise AssertionError("unreachable")

    try:
        limit = int(request.args.get("limit", 12))
    except (TypeError, ValueError) as exc:
        abort(400, description="invalid limit")
        raise AssertionError("unreachable") from exc

    limit = max(1, min(limit, 50))

    if not await _db().messages.message_exists(user["id"], msg_id):
        abort(404, description="message not found")
        raise AssertionError("unreachable")

    query = (request.args.get("q") or "").strip()[:MAX_TAG_LENGTH]

    existing = await _db().tags.get_tags_for_message(user["id"], msg_id, dek)
    excluded: set[str] = set()
    for tag in existing:
        name = (tag.get("name") or "").strip()
        if not name:
            continue
        normalized = name.lower()
        excluded.add(normalized)
        if normalized.startswith("#"):
            excluded.add(normalized[1:])
        else:
            excluded.add(f"#{normalized}")

    results = await _db().tags.search_tags(
        user["id"],
        dek,
        limit=limit,
        prefix=query or None,
        exclude_names=excluded,
    )

    payload = []
    seen: set[str] = set()
    prefix_lower = query.lower()
    prefix_plain = prefix_lower[1:] if prefix_lower.startswith("#") else prefix_lower
    for entry in results:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        if not name.startswith("#"):
            name = f"#{name}"
        name = name[:MAX_TAG_LENGTH]
        normalized = name.lower()
        if prefix_lower:
            matches = normalized.startswith(prefix_lower)
            if not matches and prefix_plain:
                plain = normalized[1:] if normalized.startswith("#") else normalized
                matches = plain.startswith(prefix_plain)
            if not matches:
                continue
        if normalized in excluded:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        payload.append({"name": name, "hash": entry.get("hash")})

    return jsonify({"results": payload})
