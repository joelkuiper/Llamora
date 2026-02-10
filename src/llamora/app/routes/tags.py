from quart import Blueprint, request, abort, render_template, jsonify
from llamora.app.services.container import get_services, get_tag_service
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.session_context import get_session_context
from llamora.app.services.tag_summary import generate_tag_summary
from llamora.app.services.time import local_date
from llamora.settings import settings
from llamora.app.util.frecency import (
    DEFAULT_FRECENCY_DECAY,
    resolve_frecency_lambda,
)
from llamora.app.routes.helpers import ensure_entry_exists, require_user_and_dek

tags_bp = Blueprint("tags", __name__)


def _tags():
    return get_tag_service()


@tags_bp.delete("/t/<entry_id>/<tag_hash>")
@login_required
async def remove_tag(entry_id: str, tag_hash: str):
    session = get_session_context()
    user = await session.require_user()
    db = get_services().db
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc
    created_date = await db.entries.get_entry_date(user["id"], entry_id)
    client_today = local_date().isoformat()
    await db.tags.unlink_tag_entry(
        user["id"],
        tag_hash_bytes,
        entry_id,
        created_date=created_date,
        client_today=client_today,
    )
    return "<span class='tag-tombstone'></span>"


@tags_bp.post("/t/<entry_id>")
@login_required
async def add_tag(entry_id: str):
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
    await ensure_entry_exists(db, user["id"], entry_id)
    tag_hash = await db.tags.resolve_or_create_tag(user["id"], canonical, dek)
    created_date = await db.entries.get_entry_date(user["id"], entry_id)
    client_today = local_date().isoformat()
    await db.tags.xref_tag_entry(
        user["id"],
        tag_hash,
        entry_id,
        created_date=created_date,
        client_today=client_today,
    )
    html = await render_template(
        "partials/tag_item.html",
        tag=canonical,
        tag_hash=tag_hash.hex(),
        entry_id=entry_id,
    )
    return html


@tags_bp.get("/t/suggestions/<entry_id>")
@login_required
async def get_tag_suggestions(entry_id: str):
    _, user, dek = await require_user_and_dek()
    decay_constant = resolve_frecency_lambda(
        request.args.get("lambda"), default=DEFAULT_FRECENCY_DECAY
    )
    llm = get_services().llm_service.llm

    max_tag_length = int(settings.LIMITS.max_tag_length)
    raw_query = (request.args.get("q") or "").strip()[:max_tag_length]
    query_canonical = raw_query.lstrip("#").strip()
    query_canonical = query_canonical[:max_tag_length].strip()

    limit = request.args.get("limit")
    clamped_limit: int | None = None
    if limit is not None:
        try:
            clamped_limit = int(limit)
        except (TypeError, ValueError) as exc:
            abort(400, description="invalid limit")
            raise AssertionError("unreachable") from exc
        clamped_limit = max(1, min(clamped_limit, 50))

    suggestions = await _tags().suggest_for_entry(
        user["id"],
        entry_id,
        dek,
        llm=llm,
        query=query_canonical or None,
        limit=clamped_limit,
        frecency_limit=3,
        decay_constant=decay_constant,
    )
    if suggestions is None:
        abort(404, description="entry not found")
        raise AssertionError("unreachable")

    wants_json = request.accept_mimetypes.best == "application/json"
    if wants_json:
        payload = [
            {"name": tag, "display": _tags().display(tag)} for tag in suggestions
        ]
        return jsonify({"results": payload})

    html = await render_template(
        "partials/tag_suggestions.html",
        suggestions=suggestions,
        entry_id=entry_id,
    )
    return html


@tags_bp.get("/t/detail/<tag_hash>")
@login_required
async def tag_detail(tag_hash: str):
    _, user, dek = await require_user_and_dek()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    page_size = 12
    overview = await _tags().get_tag_overview(
        user["id"],
        dek,
        tag_hash_bytes,
        limit=page_size,
    )
    if overview is None:
        abort(404, description="tag not found")
        raise AssertionError("unreachable")

    entry_id = (request.args.get("entry_id") or "").strip() or None
    html = await render_template(
        "partials/tag_detail_body.html",
        tag=overview,
        entries=overview.entries,
        has_more=overview.has_more,
        next_cursor=overview.next_cursor,
        page_size=page_size,
        entry_id=entry_id,
    )
    return html


@tags_bp.get("/t/detail/<tag_hash>/entries")
@login_required
async def tag_detail_entries(tag_hash: str):
    _, user, dek = await require_user_and_dek()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    cursor = (request.args.get("cursor") or "").strip() or None
    try:
        page_size = int(request.args.get("limit") or 12)
    except (TypeError, ValueError):
        page_size = 12
    page_size = max(1, min(page_size, 50))

    entries, next_cursor, has_more = await _tags().get_tag_entries_page(
        user["id"],
        dek,
        tag_hash_bytes,
        limit=page_size,
        cursor=cursor,
    )

    if not entries:
        return ""

    return await render_template(
        "partials/tag_detail_entries_chunk.html",
        entries=entries,
        has_more=has_more,
        next_cursor=next_cursor,
        tag_hash=tag_hash,
        page_size=page_size,
    )


@tags_bp.get("/t/detail/<tag_hash>/summary")
@login_required
async def tag_detail_summary(tag_hash: str):
    _, user, dek = await require_user_and_dek()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    overview = await _tags().get_tag_overview(
        user["id"],
        dek,
        tag_hash_bytes,
        limit=12,
    )
    if overview is None:
        abort(404, description="tag not found")
        raise AssertionError("unreachable")

    llm = get_services().llm_service.llm
    summary = await generate_tag_summary(
        llm,
        overview.name,
        overview.count,
        overview.last_used,
        overview.entries,
        cache_key=f"{user['id']}:{tag_hash}",
    )
    html = await render_template(
        "partials/tag_detail_summary.html",
        summary=summary,
    )
    return html
