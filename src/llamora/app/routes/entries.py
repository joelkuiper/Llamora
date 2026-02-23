"""CRUD and rendering endpoints for diary entries.

For SSE streaming endpoints, see entries_stream.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Mapping

from quart import (
    Blueprint,
    Response,
    abort,
    make_response,
    render_template,
    request,
    url_for,
)

from llamora.app.routes.helpers import (
    abort_http,
    build_view_state,
    build_cache_invalidation_trigger,
    dump_hx_trigger_header,
    ensure_entry_exists,
    is_htmx_request,
    require_encryption_context,
    require_iso_date,
)
from llamora.app.services.cache_registry import (
    MUTATION_ENTRY_CHANGED,
    MUTATION_ENTRY_CREATED,
)
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.container import get_services
from llamora.app.services.entry_context import get_entries_context
from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.services.time import get_timezone, local_date
from llamora.app.util.tags import replace_emoji_shortcodes
from llamora.settings import settings

entries_bp = Blueprint("entries", __name__)

logger = logging.getLogger(__name__)


def _entry_day(entry: Mapping[str, Any], *, today: str) -> str:
    return str(entry.get("created_date") or today)


def _entry_text_html(entry: Mapping[str, Any]) -> str:
    return str(entry.get("text_html") or render_markdown_to_html(entry.get("text", "")))


def _build_entry_payload(
    entry_id: str,
    entry: Mapping[str, Any],
    *,
    tags: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": entry_id,
        "role": entry.get("role"),
        "text": entry.get("text", ""),
        "text_html": _entry_text_html(entry),
        "meta": entry.get("meta", {}),
        "created_at": entry.get("created_at"),
    }
    if tags is not None:
        payload["tags"] = tags
    return payload


async def _load_entry_or_404(
    *,
    db: Any,
    ctx: Any,
    user_id: str,
    entry_id: str,
) -> Mapping[str, Any]:
    await ensure_entry_exists(db, user_id, entry_id)
    entries = await db.entries.get_entries_by_ids(ctx, [entry_id])
    if not entries:
        abort_http(404, "Entry not found.")
    return entries[0]


def _require_user_entry(
    entry: Mapping[str, Any], *, editable_only_today: bool = False
) -> None:
    if entry.get("role") != "user":
        abort_http(403, "Only user entries can be edited.")
    if editable_only_today:
        today = local_date().isoformat()
        day = _entry_day(entry, today=today)
        if day != today:
            abort_http(403, "Editing is available on the current day only.")


def _set_cache_invalidation_header(
    response: Response,
    *,
    mutation: str,
    reason: str,
    created_dates: tuple[str, ...],
    tag_hashes: tuple[str, ...],
) -> None:
    response.headers["HX-Trigger"] = dump_hx_trigger_header(
        build_cache_invalidation_trigger(
            mutation=mutation,
            reason=reason,
            created_dates=created_dates,
            tag_hashes=tag_hashes,
        )
    )


async def render_entries(
    date: str,
    *,
    oob: bool = False,
    scroll_target: str | None = None,
    hx_push_url: str | None = None,
    view_kind: str = "day",
) -> Response:
    _, user, ctx = await require_encryption_context()
    context = await get_entries_context(ctx, user, date)
    is_htmx = is_htmx_request()
    html = await render_template(
        "components/entries/entries.html",
        day=date,
        oob=oob,
        user=user,
        scroll_target=scroll_target,
        view_kind=view_kind,
        is_htmx=is_htmx,
        **context,
    )
    if is_htmx:
        vs_html = await render_template(
            "components/shared/view_state.html",
            view_state=build_view_state(view="diary", day=date, target=scroll_target),
            oob_view_state=True,
        )
        html = html + "\n" + vs_html

    resp = await make_response(html, 200)
    assert isinstance(resp, Response)
    if hx_push_url:
        push_url = hx_push_url
        if scroll_target:
            separator = "&" if "?" in push_url else "?"
            push_url = f"{push_url}{separator}target={scroll_target}"
        resp.headers["HX-Push-Url"] = push_url
    await get_services().db.users.update_state(user["id"], active_date=date)
    return resp


@entries_bp.route("/e/<date>")
@login_required
async def entries_htmx(date):
    normalized_date = require_iso_date(date)
    target = request.args.get("target")
    push_url = url_for("days.day", date=normalized_date)
    return await render_entries(
        normalized_date,
        oob=False,
        scroll_target=target,
        hx_push_url=push_url,
        view_kind="day",
    )


@entries_bp.route("/e/today")
@login_required
async def entries_htmx_today():
    target = request.args.get("target")
    date = local_date().isoformat()
    push_url = url_for("days.day_today")
    return await render_entries(
        date,
        oob=False,
        scroll_target=target,
        hx_push_url=push_url,
        view_kind="today",
    )


@entries_bp.get("/e/entry-tags/<entry_id>")
@login_required
async def entry_tags(entry_id: str):
    _, user, ctx = await require_encryption_context()
    db = get_services().db
    await ensure_entry_exists(db, user["id"], entry_id)
    tags = await db.tags.get_tags_for_entry(ctx, entry_id)
    html = await render_template(
        "components/entries/entry_tags_wrapper.html",
        entry_id=entry_id,
        tags=tags,
        hidden=True,
    )
    return html


@entries_bp.route("/e/entry/<entry_id>", methods=["DELETE"])
@login_required
async def delete_entry(entry_id: str):
    _, user, _ctx = await require_encryption_context()
    db = get_services().db
    await ensure_entry_exists(db, user["id"], entry_id)
    deleted_ids, root_role = await db.entries.delete_entry(user["id"], entry_id)
    if deleted_ids:
        asyncio.create_task(
            _safe_search_delete(user["id"], deleted_ids),
            name=f"search-delete-{entry_id}",
        )
    if root_role == "user":
        oob_targets = [f"entry-responses-{entry_id}"]
    else:
        oob_targets = []
    oob_deletes = "\n".join(
        f'<div id="{target_id}" hx-swap-oob="delete"></div>'
        for target_id in oob_targets
    )
    response = await make_response(oob_deletes, 200)
    response.headers["HX-Trigger"] = json.dumps({"entries:changed": True})
    return response


@entries_bp.route("/e/entry/<entry_id>", methods=["PUT", "PATCH"])
@login_required
async def update_entry(entry_id: str):
    form = await request.form
    text = replace_emoji_shortcodes(form.get("text", ""))
    _, user, ctx = await require_encryption_context()
    uid = user["id"]
    db = get_services().db

    max_len = int(settings.LIMITS.max_message_length)
    if not text.strip() or len(text) > max_len:
        abort(400, description="Entry is empty or too long.")

    current = await _load_entry_or_404(db=db, ctx=ctx, user_id=uid, entry_id=entry_id)
    _require_user_entry(current)

    updated = await db.entries.update_entry_text(
        ctx, entry_id, text, meta=current.get("meta", {})
    )
    if not updated:
        abort(404, description="Entry not found.")

    asyncio.create_task(
        _safe_search_reindex(ctx, entry_id, text),
        name=f"search-reindex-{entry_id}",
    )

    tags = await db.tags.get_tags_for_entry(ctx, entry_id)
    today = local_date().isoformat()
    day = _entry_day(updated, today=today)
    entry_payload = _build_entry_payload(entry_id, updated, tags=tags)
    html = await render_template(
        "components/entries/entry_main_only.html",
        entry=entry_payload,
        day=day,
        is_today=day == today,
    )
    tag_hashes = tuple(
        str(tag.get("hash") or "").strip() for tag in tags if tag.get("hash")
    )
    response = await make_response(html, 200)
    _set_cache_invalidation_header(
        response,
        mutation=MUTATION_ENTRY_CHANGED,
        reason="entry.changed",
        created_dates=(day,),
        tag_hashes=tag_hashes,
    )
    return response


@entries_bp.get("/e/entry/<entry_id>/edit")
@login_required
async def entry_edit(entry_id: str):
    _, user, ctx = await require_encryption_context()
    uid = user["id"]
    db = get_services().db
    entry = await _load_entry_or_404(db=db, ctx=ctx, user_id=uid, entry_id=entry_id)
    _require_user_entry(entry, editable_only_today=True)
    today = local_date().isoformat()
    day = _entry_day(entry, today=today)
    entry_payload = _build_entry_payload(entry_id, entry)
    return await render_template(
        "components/entries/entry_edit_main_only.html",
        entry=entry_payload,
        day=day,
        is_today=True,
    )


@entries_bp.get("/e/entry/<entry_id>/main")
@login_required
async def entry_main(entry_id: str):
    _, user, ctx = await require_encryption_context()
    uid = user["id"]
    db = get_services().db
    entry = await _load_entry_or_404(db=db, ctx=ctx, user_id=uid, entry_id=entry_id)
    tags: list[Mapping[str, Any]] = []
    if entry.get("role") == "user":
        tags = await db.tags.get_tags_for_entry(ctx, entry_id)
    today = local_date().isoformat()
    day = _entry_day(entry, today=today)
    entry_payload = _build_entry_payload(entry_id, entry, tags=tags)
    return await render_template(
        "components/entries/entry_main_only.html",
        entry=entry_payload,
        day=day,
        is_today=day == today,
    )


@entries_bp.route("/e/<date>/entry", methods=["POST"])
@login_required
async def send_entry(date):
    form = await request.form
    user_text = replace_emoji_shortcodes(form.get("text", "")).strip()
    user_time = form.get("user_time")
    _, user, ctx = await require_encryption_context()

    max_len = int(settings.LIMITS.max_message_length)

    if not user_text or len(user_text) > max_len:
        abort(400, description="Entry is empty or too long.")

    tz = get_timezone()
    created_at = None
    created_date = date
    if user_time:
        try:
            dt = datetime.fromisoformat(user_time.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(tz))
            created_at = dt.isoformat()
            created_date = dt.astimezone(ZoneInfo(tz)).date().isoformat()
        except Exception:
            logger.warning("Invalid user_time format: %s", user_time)
            abort_http(400, "Invalid user_time timestamp.")
    try:
        entry_id = await get_services().db.entries.append_entry(
            ctx,
            "user",
            user_text,
            created_at=created_at,
            created_date=created_date,
        )
        logger.debug("Saved entry %s", entry_id)
    except Exception:
        logger.exception("Failed to save entry")
        raise

    created_at = created_at or datetime.now(timezone.utc).isoformat()
    entry_payload = {
        "id": entry_id,
        "role": "user",
        "text": user_text,
        "text_html": render_markdown_to_html(user_text),
        "meta": {},
        "tags": [],
        "created_at": created_at,
    }
    html = await render_template(
        "components/entries/entries_list.html",
        entries=[{"entry": entry_payload, "responses": []}],
        day=created_date,
        is_today=created_date == local_date().isoformat(),
    )
    response = await make_response(html, 200)
    _set_cache_invalidation_header(
        response,
        mutation=MUTATION_ENTRY_CREATED,
        reason="entry.created",
        created_dates=(created_date,),
        tag_hashes=(),
    )
    return response


@entries_bp.route("/e/<date>/response/<entry_id>", methods=["POST"])
@login_required
async def request_response(date, entry_id: str):
    normalized_date = require_iso_date(date)
    form = await request.form
    user_time = form.get("user_time")
    _, user, _ctx = await require_encryption_context()
    uid = user["id"]

    await ensure_entry_exists(get_services().db, uid, entry_id)
    actual_date = await get_services().db.entries.get_entry_date(uid, entry_id)
    if actual_date is None:
        abort(404, description="Entry not found.")

    stream_html = await render_template(
        "components/entries/entry_response_stream_item.html",
        entry_id=entry_id,
        day=actual_date or normalized_date,
        user_time=user_time,
    )
    actions_html = await render_template(
        "components/entries/entry_actions_item.html",
        entry_id=entry_id,
        day=actual_date or normalized_date,
        is_today=normalized_date == local_date().isoformat(),
        stop_url=url_for("entries_stream.stop_response", entry_id=entry_id),
        response_active=True,
    )
    return Response(
        f"{stream_html}\n{actions_html}",
        status=200,
        mimetype="text/html",
    )


@entries_bp.get("/e/actions/<entry_id>")
@login_required
async def entry_actions_item(entry_id: str):
    _, user, _ctx = await require_encryption_context()
    uid = user["id"]
    await ensure_entry_exists(get_services().db, uid, entry_id)
    actual_date = await get_services().db.entries.get_entry_date(uid, entry_id)
    if actual_date is None:
        abort(404, description="Entry not found.")
    html = await render_template(
        "components/entries/entry_actions_item.html",
        entry_id=entry_id,
        day=actual_date,
        is_today=actual_date == local_date().isoformat(),
        stop_url=None,
        response_active=False,
    )
    return Response(html, status=200, mimetype="text/html")


async def _safe_search_delete(uid: str, ids: list[str]) -> None:
    try:
        await get_services().search_api.delete_entries(uid, ids)
    except Exception:
        logger.exception("Background search delete failed for %s", ids)


async def _safe_search_reindex(ctx, entry_id: str, text: str) -> None:
    try:
        api = get_services().search_api
        await api.delete_entries(ctx.user_id, [entry_id])
        await api.enqueue_index_job(ctx, entry_id, text)
    except Exception:
        logger.exception("Background search reindex failed for %s", entry_id)
