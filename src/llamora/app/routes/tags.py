import asyncio
import datetime as dt
import json

from quart import (
    Blueprint,
    request,
    abort,
    render_template,
    url_for,
    make_response,
)
from llamora.app.services.container import (
    get_services,
    get_lockbox_store,
    get_summarize_service,
    get_tag_service,
)
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.tag_service import TagsViewData
from llamora.app.services.tag_presenter import (
    PresentedTagsViewData,
    present_archive_detail,
    present_archive_entries,
    present_tags_view_data,
)
from llamora.app.services.tag_summary import generate_tag_summary
from llamora.app.services.activity_heatmap import get_tag_activity_heatmap
from llamora.app.services.crypto import CryptoContext
from llamora.app.services.time import local_date
from llamora.settings import settings
from llamora.app.routes.helpers import require_iso_date
from llamora.app.routes.helpers import (
    DEFAULT_TAGS_SORT_DIR,
    DEFAULT_TAGS_SORT_KIND,
    build_tags_context_query,
    build_view_state,
    ensure_entry_exists,
    get_summary_timeout_seconds,
    normalize_tags_sort,
    require_encryption_context,
)
from llamora.app.services.cache_registry import (
    MUTATION_TAG_DELETED,
    MUTATION_TAG_LINK_CHANGED,
    build_mutation_lineage_plan,
    to_client_payload,
)
from llamora.app.util.tags import emoji_shortcode, suggest_emoji_shortcodes


tags_bp = Blueprint("tags", __name__)
DEFAULT_TAG_ENTRIES_LIMIT = 12


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
    raw_day = str(request.args.get("day") or "").strip()
    if not raw_day:
        return None
    day = _resolve_view_day(raw_day)
    params: dict[str, str] = {"day": day}
    for key in ("tag", "target"):
        value = str(request.args.get(key) or "").strip()
        if value:
            params[key] = value
    return {"view": "tags", "day": day, "params": params}


def _build_view_context_query(
    context: dict[str, str | dict[str, str]] | None,
) -> str:
    if not context:
        return ""
    payload = context.get("params")
    if not isinstance(payload, dict):
        return ""
    return build_tags_context_query(
        day=str(payload.get("day") or ""),
        tag=str(payload.get("tag") or ""),
        target=str(payload.get("target") or ""),
    )


def _catalog_payload_from_index_items(
    items: tuple[object, ...],
) -> list[dict[str, str | int]]:
    payload: list[dict[str, str | int]] = []
    for item in items:
        name = str(getattr(item, "name", "") or "").strip()
        if not name:
            continue
        shortcode = emoji_shortcode(name)
        payload.append(
            {
                "name": name,
                "hash": str(getattr(item, "hash", "") or "").strip(),
                "count": int(getattr(item, "count", 0) or 0),
                "kind": "emoji" if shortcode else "text",
                "label": shortcode or "",
            }
        )
    return payload


async def _build_activity_heatmap(
    *,
    ctx: CryptoContext,
    tag_hash_hex: str | None,
    first_used: str | None,
    offset: int,
):
    raw_hash = str(tag_hash_hex or "").strip()
    if not raw_hash:
        return None
    try:
        tag_hash = bytes.fromhex(raw_hash)
    except ValueError:
        return None
    min_date = None
    first_used_value = str(first_used or "").strip()
    if first_used_value:
        try:
            min_date = dt.date.fromisoformat(first_used_value)
        except ValueError:
            min_date = None
    return await get_tag_activity_heatmap(
        get_services().db.tags,
        ctx,
        tag_hash,
        store=get_lockbox_store(),
        months=12,
        offset=offset,
        min_date=min_date,
    )


async def _render_tags_page(selected_tag: str | None):
    day = _resolve_view_day(request.args.get("day"))
    _, user, ctx = await require_encryption_context()
    services = get_services()
    today = local_date().isoformat()
    min_date = await services.db.entries.get_first_entry_date(user["id"]) or today
    is_first_day = day == min_date
    tag_service = _tags()
    sort_kind, sort_dir = normalize_tags_sort(
        sort_kind=DEFAULT_TAGS_SORT_KIND,
        sort_dir=DEFAULT_TAGS_SORT_DIR,
    )
    selected = tag_service.normalize_tag_query(
        selected_tag or (request.args.get("tag") or "")
    )
    tags_view_data = await tag_service.get_tags_view_data(
        ctx,
        selected,
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        entry_limit=DEFAULT_TAG_ENTRIES_LIMIT,
    )
    presented_tags_view = present_tags_view_data(tags_view_data)
    selected = tags_view_data.selected_tag or selected
    heatmap_offset = 0
    activity_heatmap = await _build_activity_heatmap(
        ctx=ctx,
        tag_hash_hex=tags_view_data.detail.hash if tags_view_data.detail else None,
        first_used=tags_view_data.detail.first_used if tags_view_data.detail else None,
        offset=heatmap_offset,
    )

    is_hx_main_content = bool(
        request.headers.get("HX-Request")
        and request.headers.get("HX-Target") == "main-content"
    )

    tags_index_payload: list[dict[str, str | int]] = []
    if not is_hx_main_content:
        tags_index_payload = _catalog_payload_from_index_items(tags_view_data.tags)

    target_param = (request.args.get("target") or "").strip() or None
    context = {
        "day": day,
        "is_today": day == today,
        "today": today,
        "min_date": min_date,
        "is_first_day": is_first_day,
        "view": "tags",
        "tags_view": presented_tags_view,
        "selected_tag": selected,
        "tags_sort_kind": sort_kind,
        "tags_sort_dir": sort_dir,
        "tags_catalog_items": tags_index_payload,
        "target": target_param,
        "tags_day_query": build_tags_context_query(day=day),
        "tags_selected_query": build_tags_context_query(day=day, tag=selected),
        "activity_heatmap": activity_heatmap,
        "heatmap_offset": heatmap_offset,
        "view_state": build_view_state(
            view="tags",
            day=day,
            selected_tag=selected,
            target=target_param,
        ),
    }
    if request.headers.get("HX-Request"):
        target_id = request.headers.get("HX-Target")
        if target_id == "main-content":
            html = await render_template(
                "components/shared/main_content.html", **context
            )
            return await make_response(html, 200)
    html = await render_template("pages/index.html", **context)
    return await make_response(html, 200)


@tags_bp.get("/t")
@login_required
async def tags_view_page():
    return await _render_tags_page(None)


@tags_bp.get("/t/<path:tag>")
@login_required
async def tags_view_tag(tag: str):
    return await _render_tags_page(tag)


@tags_bp.get("/emoji/suggest")
@login_required
async def emoji_shortcodes_suggest():
    query = str(request.args.get("q") or "").strip()
    limit = _parse_positive_int(
        request.args.get("limit"), default=12, min_value=1, max_value=64
    )
    return {
        "suggestions": suggest_emoji_shortcodes(query, limit=limit),
    }


async def _load_tags_view_from_context(
    ctx: CryptoContext,
    context: dict[str, str | dict[str, str]],
) -> tuple[TagsViewData, int]:
    params = context.get("params")
    if not isinstance(params, dict):
        raise ValueError("invalid context params")
    sort_kind, sort_dir = normalize_tags_sort(
        sort_kind=DEFAULT_TAGS_SORT_KIND,
        sort_dir=DEFAULT_TAGS_SORT_DIR,
    )
    entries_limit = DEFAULT_TAG_ENTRIES_LIMIT
    selected_tag = _tags().normalize_tag_query(params.get("tag"))
    tags_view = await _tags().get_tags_view_data(
        ctx,
        selected_tag,
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        entry_limit=entries_limit,
    )
    return tags_view, entries_limit


async def _render_tags_detail_and_list_oob_updates(
    ctx: CryptoContext,
    context: dict[str, str | dict[str, str]],
) -> str:
    tags_view, entries_limit = await _load_tags_view_from_context(ctx, context)
    sort_kind, sort_dir = normalize_tags_sort(
        sort_kind=DEFAULT_TAGS_SORT_KIND,
        sort_dir=DEFAULT_TAGS_SORT_DIR,
    )
    presented_tags_view = present_tags_view_data(tags_view)
    return await render_template(
        "components/tags/detail.html",
        day=str(context["day"]),
        tags_view=presented_tags_view,
        selected_tag=tags_view.selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        entries_limit=entries_limit,
        oob_detail=True,
        oob_view_state=True,
        tags_day_query=build_tags_context_query(day=str(context["day"])),
        tags_selected_query=build_tags_context_query(
            day=str(context["day"]),
            tag=tags_view.selected_tag,
        ),
        view_state=build_view_state(
            view="tags",
            day=str(context["day"]),
            selected_tag=tags_view.selected_tag,
        ),
        today=local_date().isoformat(),
    )


async def _render_view_oob_updates(
    ctx: CryptoContext,
    context: dict[str, str | dict[str, str]] | None,
) -> str:
    if not context:
        return ""
    view = str(context.get("view") or "").strip().lower()
    if view == "tags":
        return await _render_tags_detail_and_list_oob_updates(ctx, context)
    return ""


@tags_bp.delete("/t/entry/<entry_id>/<tag_hash>")
@login_required
async def remove_tag(entry_id: str, tag_hash: str):
    _, user, ctx = await require_encryption_context()
    db = get_services().db
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc
    context = _parse_view_context()
    context_view = str(context.get("view") or "").strip().lower() if context else ""

    created_date = await db.entries.get_entry_date(user["id"], entry_id)
    await db.tags.unlink_tag_entry(
        user["id"],
        tag_hash_bytes,
        entry_id,
        created_date=created_date,
    )
    tag_info = await db.tags.get_tag_info(ctx, tag_hash_bytes)
    tag_name = _tags().display(str(tag_info.get("name") or "")) if tag_info else ""
    tag_count = int(tag_info.get("count") or 0) if tag_info else 0

    html = "<span class='tag-tombstone'></span>"
    if context and context_view != "tags":
        oob = await _render_view_oob_updates(ctx, context)
        if oob:
            html = f"{html}\n{oob}"
    response = await make_response(html)

    if tag_name:
        tag_label = emoji_shortcode(tag_name) or ""
        tag_kind = "emoji" if tag_label else "text"
        lineage_plan = build_mutation_lineage_plan(
            mutation=MUTATION_TAG_LINK_CHANGED,
            reason="tag.link.changed",
            created_dates=(created_date,) if created_date else (),
            tag_hashes=(tag_hash,),
        )
        invalidation_keys = to_client_payload(lineage_plan.invalidations)
        response.headers["HX-Trigger"] = json.dumps(
            {
                "tags:tag-count-updated": {
                    "tag": tag_name,
                    "tag_hash": tag_hash,
                    "count": tag_count,
                    "entry_id": entry_id,
                    "action": "remove",
                    "tag_kind": tag_kind,
                    "tag_label": tag_label,
                },
                "cache:invalidate": {
                    "reason": "tag.link.changed",
                    "keys": invalidation_keys,
                },
            }
        )
    return response


@tags_bp.post("/t/entry/<entry_id>")
@login_required
async def add_tag(entry_id: str):
    _, user, ctx = await require_encryption_context()
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
    tag_hash = await db.tags.resolve_or_create_tag(ctx, canonical)
    tag_hash_hex = tag_hash.hex()
    existing_tags = await db.tags.get_tags_for_entry(ctx, entry_id)
    if any(str(tag.get("hash") or "").strip() == tag_hash_hex for tag in existing_tags):
        return await make_response("", 200)
    created_date = await db.entries.get_entry_date(user["id"], entry_id)
    await db.tags.xref_tag_entry(
        user["id"],
        tag_hash,
        entry_id,
        created_date=created_date,
    )
    context = _parse_view_context()
    context_query = _build_view_context_query(context)
    html = await render_template(
        "components/tags/tag_item.html",
        tag=canonical,
        tag_hash=tag_hash_hex,
        entry_id=entry_id,
        context_query=context_query,
    )
    tag_info = await db.tags.get_tag_info(ctx, tag_hash)
    tag_name = (
        _tags().display(str(tag_info.get("name") or ""))
        if tag_info
        else _tags().display(canonical)
    )
    tag_count = int(tag_info.get("count") or 0) if tag_info else 0

    context_view = str(context.get("view") or "").strip().lower() if context else ""
    if context and context_view != "tags":
        oob = await _render_view_oob_updates(ctx, context)
        if oob:
            html = f"{html}\n{oob}"
    response = await make_response(html)

    if tag_name:
        tag_label = emoji_shortcode(tag_name) or ""
        tag_kind = "emoji" if tag_label else "text"
        lineage_plan = build_mutation_lineage_plan(
            mutation=MUTATION_TAG_LINK_CHANGED,
            reason="tag.link.changed",
            created_dates=(created_date,) if created_date else (),
            tag_hashes=(tag_hash_hex,),
        )
        invalidation_keys = to_client_payload(lineage_plan.invalidations)
        response.headers["HX-Trigger"] = json.dumps(
            {
                "tags:tag-count-updated": {
                    "tag": tag_name,
                    "tag_hash": tag_hash_hex,
                    "count": tag_count,
                    "entry_id": entry_id,
                    "action": "add",
                    "tag_kind": tag_kind,
                    "tag_label": tag_label,
                },
                "cache:invalidate": {
                    "reason": "tag.link.changed",
                    "keys": invalidation_keys,
                },
            }
        )
    return response


@tags_bp.get("/t/entry/<entry_id>/suggestions")
@login_required
async def get_tag_suggestions(entry_id: str):
    _, _user, ctx = await require_encryption_context()
    llm = get_services().llm_service.llm

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
        ctx,
        entry_id,
        llm=llm,
        limit=clamped_limit,
        frecency_limit=3,
    )
    if suggestions is None:
        abort(404, description="entry not found")
        raise AssertionError("unreachable")

    suggestion_items = []
    for tag in suggestions:
        name = str(tag or "").strip()
        if not name:
            continue
        is_text = all(
            ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "-" for ch in name
        )
        suggestion_items.append(
            {
                "name": name,
                "display": _tags().display(name),
                "kind": "text" if is_text else "emoji",
            }
        )

    html = await render_template(
        "components/tags/tag_suggestions.html",
        suggestions=suggestion_items,
        entry_id=entry_id,
        add_tag_url=f"{url_for('tags.add_tag', entry_id=entry_id)}{_build_view_context_query(_parse_view_context())}",
    )
    return html


@tags_bp.get("/t/detail/<tag_hash>")
@login_required
async def tag_detail(tag_hash: str):
    _, user, ctx = await require_encryption_context()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    page_size = 12
    overview = await _tags().get_tag_overview(
        ctx,
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
        "components/tags/tag_detail_body.html",
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
    _, user, ctx = await require_encryption_context()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    tag_service = _tags()
    sort_kind, sort_dir = normalize_tags_sort(
        sort_kind=DEFAULT_TAGS_SORT_KIND,
        sort_dir=DEFAULT_TAGS_SORT_DIR,
    )

    index_before_delete = await tag_service.get_tags_index_items(
        ctx,
        sort_kind=sort_kind,
        sort_dir=sort_dir,
    )
    adjacent_tag: str | None = None
    for idx, item in enumerate(index_before_delete):
        if item.hash != tag_hash:
            continue
        if idx + 1 < len(index_before_delete):
            adjacent_tag = index_before_delete[idx + 1].name
        elif idx > 0:
            adjacent_tag = index_before_delete[idx - 1].name
        break

    requested_tag = (request.args.get("tag") or "").strip() or None

    removed_tag_info = await get_services().db.tags.get_tag_info(ctx, tag_hash_bytes)
    removed_tag_name = (
        _tags().display(str(removed_tag_info.get("name") or ""))
        if removed_tag_info
        else ""
    )
    affected_entries = await get_services().db.tags.delete_tag_everywhere(
        user["id"],
        tag_hash_bytes,
    )
    affected_dates = tuple(
        sorted(
            {str(created or "").strip() for _, created in affected_entries if created}
        )
    )

    if requested_tag and removed_tag_name:
        requested_norm = tag_service.normalize_tag_query(requested_tag)
        removed_norm = tag_service.normalize_tag_query(removed_tag_name)
        if requested_norm and removed_norm and requested_norm != removed_norm:
            adjacent_tag = requested_tag

    tags_view = await tag_service.get_tags_view_data(
        ctx,
        adjacent_tag,
        sort_kind=sort_kind,
        sort_dir=sort_dir,
        entry_limit=DEFAULT_TAG_ENTRIES_LIMIT,
    )
    selected_tag = tags_view.selected_tag
    entries_limit = DEFAULT_TAG_ENTRIES_LIMIT
    presented_tags_view = present_tags_view_data(tags_view)
    html = await render_template(
        "components/tags/detail.html",
        day=day,
        tags_view=presented_tags_view,
        selected_tag=selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        entries_limit=entries_limit,
        tags_day_query=build_tags_context_query(day=day),
        tags_selected_query=build_tags_context_query(day=day, tag=selected_tag),
        today=local_date().isoformat(),
    )
    response = await make_response(html)
    if removed_tag_name:
        tag_label = emoji_shortcode(removed_tag_name) or ""
        tag_kind = "emoji" if tag_label else "text"
        lineage_plan = build_mutation_lineage_plan(
            mutation=MUTATION_TAG_DELETED,
            reason="tag.deleted",
            created_dates=affected_dates,
            tag_hashes=(tag_hash,),
        )
        response.headers["HX-Trigger"] = json.dumps(
            {
                "tags:tag-count-updated": {
                    "tag": removed_tag_name,
                    "tag_hash": tag_hash,
                    "count": 0,
                    "action": "delete",
                    "tag_kind": tag_kind,
                    "tag_label": tag_label,
                },
                "cache:invalidate": {
                    "reason": "tag.deleted",
                    "keys": to_client_payload(lineage_plan.invalidations),
                },
            }
        )
    return response


@tags_bp.get("/t/detail/<tag_hash>/entries")
@login_required
async def tag_detail_entries(tag_hash: str):
    _, user, ctx = await require_encryption_context()
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
        ctx,
        tag_hash_bytes,
        limit=page_size,
        cursor=cursor,
    )

    if not entries:
        return ""

    return await render_template(
        "components/tags/tag_detail_entries_chunk.html",
        entries=entries,
        has_more=has_more,
        next_cursor=next_cursor,
        tag_hash=tag_hash,
        page_size=page_size,
    )


@tags_bp.get("/t/detail/<tag_hash>/summary")
@login_required
async def tag_detail_summary(tag_hash: str):
    _, user, ctx = await require_encryption_context()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    overview = await _tags().get_tag_overview(
        ctx,
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

    summarize = get_summarize_service()
    summary_timeout_seconds = get_summary_timeout_seconds()
    summary_cache_key = f"tag:{tag_hash}:w{num_words}"
    summary_cache_namespace = "summary"
    summary_digest = overview.summary_digest

    if summary_digest:
        cached_html = await summarize.get_cached(
            ctx,
            summary_cache_namespace,
            summary_cache_key,
            summary_digest,
            field="html",
        )
        if cached_html:
            return cached_html

    llm = get_services().llm_service.llm
    try:
        summary = await asyncio.wait_for(
            generate_tag_summary(
                llm,
                overview.name,
                overview.count,
                overview.last_used,
                overview.entries,
                num_words=num_words,
            ),
            timeout=summary_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        abort(504, description="Summary generation timed out.")
        raise AssertionError("unreachable") from exc
    html = await render_template(
        "components/tags/tag_detail_summary.html",
        summary=summary,
        summary_digest=summary_digest,
    )
    if summary and summary_digest:
        await summarize.cache(
            ctx,
            summary_cache_namespace,
            summary_cache_key,
            summary_digest,
            html,
            field="html",
        )
    return html


@tags_bp.get("/fragments/tags/<date>/detail")
@login_required
async def tags_view_detail_fragment(date: str):
    """Return only the detail pane for a single tag.

    Accepts ``tag_hash`` for O(1) lookup or falls back to ``tag`` (name).
    Much lighter than the full tags view page.
    """

    normalized_date = require_iso_date(date)
    _, user, ctx = await require_encryption_context()
    tag_service = _tags()
    sort_kind, sort_dir = normalize_tags_sort(
        sort_kind=DEFAULT_TAGS_SORT_KIND,
        sort_dir=DEFAULT_TAGS_SORT_DIR,
    )
    restore_entry = (request.args.get("restore_entry") or "").strip() or None
    tag_name = (request.args.get("tag") or "").strip() or None
    tag_hash_hex = (request.args.get("tag_hash") or "").strip() or None

    if not tag_name and not tag_hash_hex:
        tag_items = await tag_service.get_tags_index_items(
            ctx,
            sort_kind=sort_kind,
            sort_dir=sort_dir,
        )
        if tag_items:
            first_item = tag_items[0]
            tag_name = first_item.name
            tag_hash_hex = first_item.hash

    detail = await tag_service.get_tag_detail(
        ctx,
        tag_name=tag_name,
        tag_hash_hex=tag_hash_hex,
        entry_limit=DEFAULT_TAG_ENTRIES_LIMIT,
        around_entry_id=restore_entry,
    )
    presented_detail = present_archive_detail(detail) if detail else None
    selected_tag = detail.name if detail else (tag_name or "")
    heatmap_offset = 0
    activity_heatmap = await _build_activity_heatmap(
        ctx=ctx,
        tag_hash_hex=detail.hash if detail else None,
        first_used=detail.first_used if detail else None,
        offset=heatmap_offset,
    )

    presented_tags_view = PresentedTagsViewData(
        tags=(),
        selected_tag=selected_tag,
        detail=presented_detail,
        sort_kind=sort_kind,
        sort_dir=sort_dir,
    )
    return await render_template(
        "components/tags/detail.html",
        day=normalized_date,
        tags_view=presented_tags_view,
        selected_tag=selected_tag,
        tags_sort_kind=sort_kind,
        tags_sort_dir=sort_dir,
        entries_limit=DEFAULT_TAG_ENTRIES_LIMIT,
        heatmap_offset=heatmap_offset,
        activity_heatmap=activity_heatmap,
        tags_day_query=build_tags_context_query(day=normalized_date),
        tags_selected_query=build_tags_context_query(
            day=normalized_date,
            tag=selected_tag,
        ),
        oob_view_state=True,
        view_state=build_view_state(
            view="tags",
            day=normalized_date,
            selected_tag=selected_tag,
        ),
        today=local_date().isoformat(),
    )


@tags_bp.get("/fragments/tags/<date>/heatmap")
@login_required
async def tags_view_heatmap(date: str):
    normalized_date = require_iso_date(date)
    _, _user, ctx = await require_encryption_context()
    services = get_services()
    tag_hash_raw = (request.args.get("tag_hash") or "").strip()
    heatmap_offset = _parse_positive_int(
        request.args.get("heatmap_offset"), default=0, min_value=0, max_value=240
    )
    min_date_raw = (request.args.get("min_date") or "").strip()
    activity_heatmap = None
    if tag_hash_raw:
        try:
            tag_hash = bytes.fromhex(tag_hash_raw)
        except ValueError:
            tag_hash = b""
        if tag_hash:
            min_date = None
            if min_date_raw:
                try:
                    min_date = dt.date.fromisoformat(min_date_raw)
                except ValueError:
                    min_date = None
            activity_heatmap = await get_tag_activity_heatmap(
                services.db.tags,
                ctx,
                tag_hash,
                store=get_lockbox_store(),
                months=12,
                offset=heatmap_offset,
                min_date=min_date,
            )
    return await render_template(
        "components/tags/heatmap.html",
        day=normalized_date,
        activity_heatmap=activity_heatmap,
        tag_hash=tag_hash_raw,
        heatmap_offset=heatmap_offset,
        min_date=min_date_raw,
        selected_day=normalized_date,
        today=local_date().isoformat(),
    )


@tags_bp.get("/fragments/tags/<date>/detail/<tag_hash>/entries")
@login_required
async def tags_view_detail_entries_chunk(date: str, tag_hash: str):
    normalized_date = require_iso_date(date)
    _, user, ctx = await require_encryption_context()
    try:
        tag_hash_bytes = bytes.fromhex(tag_hash)
    except ValueError as exc:
        abort(400, description="invalid tag hash")
        raise AssertionError("unreachable") from exc

    tag_service = _tags()
    entries_limit = _parse_positive_int(
        request.args.get("limit"), default=12, min_value=6, max_value=60
    )
    selected_tag = tag_service.normalize_tag_query(request.args.get("tag"))
    entries, next_cursor, has_more = await tag_service.get_archive_entries_page(
        ctx,
        [tag_hash_bytes],
        limit=entries_limit,
        cursor=(request.args.get("cursor") or "").strip() or None,
    )
    if not entries:
        return ""
    return await render_template(
        "components/tags/entries_chunk.html",
        day=normalized_date,
        entries=present_archive_entries(entries),
        selected_tag=selected_tag,
        tag_hash=tag_hash,
        has_more=has_more,
        next_cursor=next_cursor,
        entries_limit=entries_limit,
        page_size=entries_limit,
        tags_day_query=build_tags_context_query(day=normalized_date),
        tags_selected_query=build_tags_context_query(
            day=normalized_date,
            tag=selected_tag,
        ),
        today=local_date().isoformat(),
    )
