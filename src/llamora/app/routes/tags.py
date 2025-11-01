from quart import Blueprint, request, abort, render_template, jsonify
from llamora.app.services.container import get_services
from llamora.app.services.auth_helpers import login_required, get_current_user, get_dek
from llamora.app.util.tags import canonicalize, display
from llamora.settings import settings
from llamora.app.utils.frecency import (
    DEFAULT_FRECENCY_DECAY,
    resolve_frecency_lambda,
)

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
    raw_tag = (form.get("tag") or "").strip()
    max_tag_length = int(settings.LIMITS.max_tag_length)
    if len(raw_tag) > max_tag_length:
        raw_tag = raw_tag[:max_tag_length]
    try:
        canonical = canonicalize(raw_tag)
    except ValueError:
        abort(400, description="empty tag")
        raise AssertionError("unreachable")
    dek = get_dek()
    if dek is None:
        abort(401, description="Missing encryption key")
        raise AssertionError("unreachable")
    if not await _db().messages.message_exists(user["id"], msg_id):
        abort(404, description="message not found")
    tag_hash = await _db().tags.resolve_or_create_tag(user["id"], canonical, dek)
    await _db().tags.xref_tag_message(user["id"], tag_hash, msg_id)
    html = await render_template(
        "partials/tag_chip.html",
        keyword=canonical,
        tag_hash=tag_hash.hex(),
        msg_id=msg_id,
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
    existing_names = {
        (t.get("name") or "").strip().lower()
        for t in existing
        if (t.get("name") or "").strip()
    }

    meta_suggestions: set[str] = set()
    for kw in keywords:
        try:
            canonical_kw = canonicalize(kw)
        except ValueError:
            continue
        meta_suggestions.add(canonical_kw)

    decay_constant = resolve_frecency_lambda(
        request.args.get("lambda"), default=DEFAULT_FRECENCY_DECAY
    )
    frecent_tags = await _db().tags.get_tag_frecency(
        user["id"], 3, decay_constant, dek
    )
    frecent_suggestions = {t["name"] for t in frecent_tags if (t.get("name"))}

    combined = meta_suggestions | frecent_suggestions
    combined = [
        name
        for name in combined
        if name and name.strip().lower() not in existing_names
    ]

    html = await render_template(
        "partials/tag_suggestions.html",
        suggestions=combined,
        msg_id=msg_id,
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

    max_tag_length = int(settings.LIMITS.max_tag_length)
    raw_query = (request.args.get("q") or "").strip()[:max_tag_length]
    query_canonical = raw_query.lstrip("#").strip()
    query_canonical = query_canonical[:max_tag_length].strip()

    existing = await _db().tags.get_tags_for_message(user["id"], msg_id, dek)
    excluded: set[str] = set()
    for tag in existing:
        name = (tag.get("name") or "").strip().lower()
        if not name:
            continue
        excluded.add(name)

    lambda_param = request.args.get("lambda")
    decay_constant = resolve_frecency_lambda(
        lambda_param, default=DEFAULT_FRECENCY_DECAY
    )

    results = await _db().tags.search_tags(
        user["id"],
        dek,
        limit=limit,
        prefix=query_canonical or None,
        lambda_=decay_constant,
        exclude_names=excluded,
    )

    payload = []
    seen: set[str] = set()
    prefix_lower = query_canonical.lower()
    for entry in results:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        canonical_name = name[:max_tag_length].strip()
        lower_name = canonical_name.lower()
        if prefix_lower:
            if not lower_name.startswith(prefix_lower):
                continue
        if lower_name in excluded:
            continue
        if lower_name in seen:
            continue
        seen.add(lower_name)
        payload.append(
            {
                "name": canonical_name,
                "display": display(canonical_name),
                "hash": entry.get("hash"),
            }
        )

    return jsonify({"results": payload})
