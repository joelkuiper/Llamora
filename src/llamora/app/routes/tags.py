from quart import Blueprint, request, abort, render_template, jsonify
from llamora.app.services.container import get_services, get_tag_service
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.session_context import get_session_context
from llamora.settings import settings
from llamora.app.util.frecency import (
    DEFAULT_FRECENCY_DECAY,
    resolve_frecency_lambda,
)
from llamora.app.routes.helpers import ensure_message_exists, require_user_and_dek

tags_bp = Blueprint("tags", __name__)


def _tags():
    return get_tag_service()


@tags_bp.delete("/t/<msg_id>/<tag_hash>")
@login_required
async def remove_tag(msg_id: str, tag_hash: str):
    session = get_session_context()
    user = await session.require_user()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc
    await get_services().db.tags.unlink_tag_message(user["id"], tag_hash_bytes, msg_id)
    return "<span class='chip-tombstone'></span>"


@tags_bp.post("/t/<msg_id>")
@login_required
async def add_tag(msg_id: str):
    _, user, dek = await require_user_and_dek()
    form = await request.form
    raw_tag = (form.get("tag") or "").strip()
    max_tag_length = int(settings.LIMITS.max_tag_length)
    if len(raw_tag) > max_tag_length:
        raw_tag = raw_tag[:max_tag_length]
    try:
        canonical = _tags().canonicalize(raw_tag)
    except ValueError:
        abort(400, description="empty tag")
        raise AssertionError("unreachable")
    db = get_services().db
    await ensure_message_exists(db, user["id"], msg_id)
    tag_hash = await db.tags.resolve_or_create_tag(user["id"], canonical, dek)
    await db.tags.xref_tag_message(user["id"], tag_hash, msg_id)
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
    _, user, dek = await require_user_and_dek()
    decay_constant = resolve_frecency_lambda(
        request.args.get("lambda"), default=DEFAULT_FRECENCY_DECAY
    )
    llm = get_services().llm_service.llm

    suggestions = await _tags().suggest_for_message(
        user["id"],
        msg_id,
        dek,
        llm=llm,
        frecency_limit=3,
        decay_constant=decay_constant,
    )
    if suggestions is None:
        abort(404, description="message not found")
        raise AssertionError("unreachable")

    html = await render_template(
        "partials/tag_suggestions.html",
        suggestions=suggestions,
        msg_id=msg_id,
    )
    return html


@tags_bp.get("/tags/autocomplete")
@login_required
async def autocomplete_tags():
    _, user, dek = await require_user_and_dek()

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

    db = get_services().db
    await ensure_message_exists(db, user["id"], msg_id)

    max_tag_length = int(settings.LIMITS.max_tag_length)
    raw_query = (request.args.get("q") or "").strip()[:max_tag_length]
    query_canonical = raw_query.lstrip("#").strip()
    query_canonical = query_canonical[:max_tag_length].strip()

    existing = await db.tags.get_tags_for_message(user["id"], msg_id, dek)
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

    results = await db.tags.search_tags(
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
                "display": _tags().display(canonical_name),
                "hash": entry.get("hash"),
            }
        )

    return jsonify({"results": payload})
