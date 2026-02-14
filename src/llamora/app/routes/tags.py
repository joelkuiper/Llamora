from quart import Blueprint, request, abort, render_template, jsonify, url_for
from urllib.parse import urlencode
from llamora.app.services.container import get_services, get_tag_service
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.tag_service import TagsViewData
from llamora.app.services.lockbox import Lockbox
from llamora.app.services.lockbox_store import LockboxStore
from llamora.app.services.tag_summary import generate_tag_summary
from llamora.app.services.time import local_date
from llamora.settings import settings
from llamora.app.routes.helpers import require_iso_date
from llamora.app.util.frecency import (
    DEFAULT_FRECENCY_DECAY,
    resolve_frecency_lambda,
)
from llamora.app.routes.helpers import ensure_entry_exists, require_user_and_dek

tags_bp = Blueprint("tags", __name__)


def _tags():
    return get_tag_service()


def _resolve_view_day(raw_day: str | None) -> str:
    fallback = local_date().isoformat()
    value = str(raw_day or "").strip()
    if not value:
        return fallback
    try:
        return require_iso_date(value)
    except Exception:
        return fallback


def _parse_positive_int(
    raw: str | None, *, default: int, min_value: int, max_value: int
) -> int:
    try:
        value = int(str(raw or "").strip() or str(default))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(value, max_value))


def _parse_view_context() -> dict[str, str | dict[str, str]] | None:
    view = str(request.args.get("view") or "").strip().lower()
    if not view:
        return None
    day = _resolve_view_day(request.args.get("day"))
    params: dict[str, str] = {"view": view, "day": day}
    for key in ("sort_kind", "sort_dir", "entries_limit", "tag", "target"):
        value = str(request.args.get(key) or "").strip()
        if value:
            params[key] = value
    return {"view": view, "day": day, "params": params}


def _build_view_context_query(
    context: dict[str, str | dict[str, str]] | None,
) -> str:
    if not context:
        return ""
    payload = context.get("params")
    if not isinstance(payload, dict):
        return ""
    return f"?{urlencode(payload)}"


async def _load_tags_view_from_context(
    user_id: str,
    dek: bytes,
    context: dict[str, str | dict[str, str]],
) -> tuple[TagsViewData, str, str, int]:
    params = context.get("params")
    if not isinstance(params, dict):
        raise ValueError("invalid context params")
    tag_service = _tags()
    sort_kind = tag_service.normalize_tags_sort_kind(params.get("sort_kind"))
    sort_dir = tag_service.normalize_tags_sort_dir(params.get("sort_dir"))
    legacy_sort = tag_service.normalize_legacy_sort(params.get("sort"))
    if legacy_sort is not None:
        sort_kind, sort_dir = legacy_sort
    entries_limit = _parse_positive_int(
        params.get("entries_limit"), default=12, min_value=6, max_value=60
    )
    selected_tag = tag_service.normalize_tag_query(params.get("tag"))
    tags_view = await tag_service.get_tags_view_data(
        user_id,
        dek,
        selected_tag,
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        entry_limit=entries_limit,
    )
    return tags_view, sort_kind, sort_dir, entries_limit


async def _render_tags_detail_and_list_oob_updates(
    user_id: str,
    dek: bytes,
    context: dict[str, str | dict[str, str]],
) -> str:
    tags_view, sort_kind, sort_dir, entries_limit = await _load_tags_view_from_context(
        user_id, dek, context
    )
    return await render_template(
        "partials/tags_view_fragment.html",
        day=str(context["day"]),
        tags_view=tags_view,
        selected_tag=tags_view.selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        include_list=True,
        entries_limit=entries_limit,
        target=None,
        oob_detail=True,
        today=local_date().isoformat(),
    )


async def _render_tags_list_oob_updates(
    user_id: str,
    dek: bytes,
    context: dict[str, str | dict[str, str]],
) -> str:
    tags_view, sort_kind, sort_dir, entries_limit = await _load_tags_view_from_context(
        user_id, dek, context
    )
    return await render_template(
        "partials/tags_view_list_oob.html",
        day=str(context["day"]),
        tags_view=tags_view,
        selected_tag=tags_view.selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        entries_limit=entries_limit,
        target=None,
    )


async def _render_view_oob_updates(
    user_id: str,
    dek: bytes,
    context: dict[str, str | dict[str, str]] | None,
    *,
    tags_mode: str = "detail_and_list",
) -> str:
    if not context:
        return ""
    view = str(context.get("view") or "").strip().lower()
    if view == "tags":
        if tags_mode == "list_only":
            return await _render_tags_list_oob_updates(user_id, dek, context)
        return await _render_tags_detail_and_list_oob_updates(user_id, dek, context)
    return ""


@tags_bp.delete("/t/<entry_id>/<tag_hash>")
@login_required
async def remove_tag(entry_id: str, tag_hash: str):
    _, user, dek = await require_user_and_dek()
    db = get_services().db
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc
    context = _parse_view_context()
    selected_tag: str | None = None
    removed_tag_name: str | None = None
    if context and str(context.get("view") or "").strip().lower() == "tags":
        params = context.get("params")
        if isinstance(params, dict):
            selected_tag = _tags().normalize_tag_query(params.get("tag"))
        if selected_tag:
            tag_info = await db.tags.get_tag_info(user["id"], tag_hash_bytes, dek)
            if tag_info:
                removed_tag_name = _tags().normalize_tag_query(tag_info.get("name"))

    created_date = await db.entries.get_entry_date(user["id"], entry_id)
    client_today = local_date().isoformat()
    await db.tags.unlink_tag_entry(
        user["id"],
        tag_hash_bytes,
        entry_id,
        created_date=created_date,
        client_today=client_today,
    )
    if not context:
        return "<span class='tag-tombstone'></span>"
    tags_mode = "detail_and_list"
    if (
        str(context.get("view") or "").strip().lower() == "tags"
        and selected_tag
        and removed_tag_name
        and selected_tag != removed_tag_name
    ):
        tags_mode = "list_only"
    oob = await _render_view_oob_updates(user["id"], dek, context, tags_mode=tags_mode)
    if not oob:
        return "<span class='tag-tombstone'></span>"
    return f"<span class='tag-tombstone'></span>\n{oob}"


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
    context = _parse_view_context()
    context_query = _build_view_context_query(context)
    html = await render_template(
        "partials/tag_item.html",
        tag=canonical,
        tag_hash=tag_hash.hex(),
        entry_id=entry_id,
        context_query=context_query,
    )
    if not context:
        return html
    oob = await _render_view_oob_updates(
        user["id"], dek, context, tags_mode="list_only"
    )
    if not oob:
        return html
    return f"{html}\n{oob}"


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
    query_canonical = ""
    if raw_query:
        try:
            query_canonical = _tags().canonicalize(raw_query)
        except ValueError:
            query_canonical = ""

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
        add_tag_url=f"{url_for('tags.add_tag', entry_id=entry_id)}{_build_view_context_query(_parse_view_context())}",
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
    detail_day = _resolve_view_day(request.args.get("day"))
    context_query = _build_view_context_query(_parse_view_context())
    html = await render_template(
        "partials/tag_detail_body.html",
        tag=overview,
        entries=overview.entries,
        has_more=overview.has_more,
        next_cursor=overview.next_cursor,
        page_size=page_size,
        entry_id=entry_id,
        detail_day=detail_day,
        context_query=context_query,
    )
    return html


@tags_bp.delete("/t/detail/<tag_hash>/trace")
@login_required
async def delete_trace(tag_hash: str):
    day = _resolve_view_day(request.args.get("day"))
    _, user, dek = await require_user_and_dek()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    tag_service = _tags()
    sort_kind = tag_service.normalize_tags_sort_kind(request.args.get("sort_kind"))
    sort_dir = tag_service.normalize_tags_sort_dir(request.args.get("sort_dir"))
    legacy_sort = tag_service.normalize_legacy_sort(request.args.get("sort"))
    if legacy_sort is not None:
        sort_kind, sort_dir = legacy_sort

    include_list = str(request.args.get("include_list") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    await get_services().db.tags.delete_tag_everywhere(
        user["id"],
        tag_hash_bytes,
        client_today=local_date().isoformat(),
    )

    tags_view = await tag_service.get_tags_view_data(
        user["id"],
        dek,
        request.args.get("tag"),
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        entry_limit=_parse_positive_int(
            request.args.get("entries_limit"), default=12, min_value=6, max_value=60
        ),
    )
    selected_tag = tags_view.selected_tag
    entries_limit = _parse_positive_int(
        request.args.get("entries_limit"), default=12, min_value=6, max_value=60
    )
    return await render_template(
        "partials/tags_view_fragment.html",
        day=day,
        tags_view=tags_view,
        selected_tag=selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        include_list=include_list,
        entries_limit=entries_limit,
        today=local_date().isoformat(),
    )


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

    try:
        num_words = int(request.args.get("num_words") or 28)
    except (TypeError, ValueError):
        num_words = 28
    num_words = max(18, min(num_words, 160))

    summary_cache_key = f"tag:{tag_hash}:w{num_words}"
    summary_cache_namespace = "summary"
    summary_digest = overview.summary_digest
    if summary_digest:
        store = LockboxStore(Lockbox(get_services().db.pool))
        cached = await store.get_json(
            user["id"],
            dek,
            summary_cache_namespace,
            summary_cache_key,
        )
        if isinstance(cached, dict):
            cached_digest = str(cached.get("digest") or "")
            cached_html = cached.get("html")
            if cached_digest == summary_digest and isinstance(cached_html, str):
                cached_html = cached_html.strip()
                if cached_html:
                    return cached_html

    llm = get_services().llm_service.llm
    summary = await generate_tag_summary(
        llm,
        overview.name,
        overview.count,
        overview.last_used,
        overview.entries,
        num_words=num_words,
    )
    html = await render_template(
        "partials/tag_detail_summary.html",
        summary=summary,
    )
    if summary and summary_digest:
        store = LockboxStore(Lockbox(get_services().db.pool))
        await store.set_json(
            user["id"],
            dek,
            summary_cache_namespace,
            summary_cache_key,
            {
                "digest": summary_digest,
                "html": html,
            },
        )
    return html


@tags_bp.get("/fragments/tags/<date>")
@login_required
async def tags_view_fragment(date: str):
    normalized_date = require_iso_date(date)
    _, user, dek = await require_user_and_dek()
    tag_service = _tags()
    sort_kind = tag_service.normalize_tags_sort_kind(request.args.get("sort_kind"))
    sort_dir = tag_service.normalize_tags_sort_dir(request.args.get("sort_dir"))
    legacy_sort = tag_service.normalize_legacy_sort(request.args.get("sort"))
    if legacy_sort is not None:
        sort_kind, sort_dir = legacy_sort
    include_list = str(request.args.get("include_list") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    entries_limit = _parse_positive_int(
        request.args.get("entries_limit"), default=12, min_value=6, max_value=60
    )
    restore_entry = (request.args.get("restore_entry") or "").strip() or None
    tags_view = await tag_service.get_tags_view_data(
        user["id"],
        dek,
        request.args.get("tag"),
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        entry_limit=entries_limit,
        around_entry_id=restore_entry,
    )
    selected_tag = tags_view.selected_tag
    return await render_template(
        "partials/tags_view_fragment.html",
        day=normalized_date,
        tags_view=tags_view,
        selected_tag=selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        include_list=include_list,
        entries_limit=entries_limit,
        target=(request.args.get("target") or "").strip() or None,
        today=local_date().isoformat(),
    )


@tags_bp.get("/fragments/tags/<date>/list")
@login_required
async def tags_view_list_fragment(date: str):
    normalized_date = require_iso_date(date)
    _, user, dek = await require_user_and_dek()
    tag_service = _tags()
    sort_kind = tag_service.normalize_tags_sort_kind(request.args.get("sort_kind"))
    sort_dir = tag_service.normalize_tags_sort_dir(request.args.get("sort_dir"))
    legacy_sort = tag_service.normalize_legacy_sort(request.args.get("sort"))
    if legacy_sort is not None:
        sort_kind, sort_dir = legacy_sort
    entries_limit = _parse_positive_int(
        request.args.get("entries_limit"), default=12, min_value=6, max_value=60
    )
    tags_view = await tag_service.get_tags_view_data(
        user["id"],
        dek,
        request.args.get("tag"),
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        entry_limit=entries_limit,
    )
    return await render_template(
        "partials/tags_view_list_fragment.html",
        day=normalized_date,
        tags_view=tags_view,
        selected_tag=tags_view.selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        entries_limit=entries_limit,
        target=(request.args.get("target") or "").strip() or None,
        today=local_date().isoformat(),
    )


@tags_bp.get("/fragments/tags/<date>/detail/<tag_hash>/entries")
@login_required
async def tags_view_detail_entries_chunk(date: str, tag_hash: str):
    normalized_date = require_iso_date(date)
    _, user, dek = await require_user_and_dek()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    tag_service = _tags()
    sort_kind = tag_service.normalize_tags_sort_kind(request.args.get("sort_kind"))
    sort_dir = tag_service.normalize_tags_sort_dir(request.args.get("sort_dir"))
    entries_limit = _parse_positive_int(
        request.args.get("limit"), default=12, min_value=6, max_value=60
    )
    entries, next_cursor, has_more = await tag_service.get_archive_entries_page(
        user["id"],
        [tag_hash_bytes],
        dek,
        limit=entries_limit,
        cursor=(request.args.get("cursor") or "").strip() or None,
    )
    if not entries:
        return ""
    return await render_template(
        "partials/tags_view_entries_chunk.html",
        day=normalized_date,
        entries=entries,
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        selected_tag=tag_service.normalize_tag_query(request.args.get("tag")),
        tag_hash=tag_hash,
        has_more=has_more,
        next_cursor=next_cursor,
        entries_limit=entries_limit,
        page_size=entries_limit,
        today=local_date().isoformat(),
    )
