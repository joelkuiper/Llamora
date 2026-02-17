"""CRUD and rendering endpoints for diary entries.

For SSE streaming endpoints, see entries_stream.py.
"""

from __future__ import annotations

import asyncio
import logging
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
    ensure_entry_exists,
    require_encryption_context,
    require_iso_date,
)
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.container import get_services
from llamora.app.services.entry_context import get_entries_context
from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.services.time import get_timezone, local_date
from llamora.settings import settings

entries_bp = Blueprint("entries", __name__)

logger = logging.getLogger(__name__)


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
    html = await render_template(
        "components/entries/entries.html",
        day=date,
        oob=oob,
        user=user,
        scroll_target=scroll_target,
        view_kind=view_kind,
        **context,
    )

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
    return Response(oob_deletes, status=200, mimetype="text/html")


@entries_bp.route("/e/entry/<entry_id>", methods=["PUT", "PATCH"])
@login_required
async def update_entry(entry_id: str):
    form = await request.form
    text = form.get("text", "")
    _, user, ctx = await require_encryption_context()
    uid = user["id"]
    db = get_services().db

    max_len = int(settings.LIMITS.max_message_length)
    if not text.strip() or len(text) > max_len:
        abort(400, description="Entry is empty or too long.")

    await ensure_entry_exists(db, uid, entry_id)

    entries = await db.entries.get_entries_by_ids(ctx, [entry_id])
    if not entries:
        abort(404, description="Entry not found.")
    current = entries[0]
    if current.get("role") != "user":
        abort(403, description="Only user entries can be edited.")

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
    entry_payload = {
        "id": entry_id,
        "role": updated.get("role"),
        "text": updated.get("text", ""),
        "text_html": render_markdown_to_html(updated.get("text", "")),
        "meta": updated.get("meta", {}),
        "tags": tags,
        "created_at": updated.get("created_at"),
    }
    today = local_date().isoformat()
    day = updated.get("created_date") or today
    html = await render_template(
        "components/entries/entry_main_only.html",
        entry=entry_payload,
        day=day,
        is_today=day == today,
    )
    invalidation_keys: list[dict[str, str]] = [
        {
            "namespace": "summary",
            "prefix": f"day:{day}",
            "reason": "entry.changed",
        }
    ]
    for tag in tags:
        tag_hash = str(tag.get("hash") or "").strip()
        if tag_hash:
            invalidation_keys.append(
                {
                    "namespace": "summary",
                    "prefix": f"tag:{tag_hash}",
                    "reason": "entry.changed",
                }
            )
    response = await make_response(html, 200)
    response.headers["HX-Trigger"] = json.dumps(
        {
            "cache:invalidate": {
                "reason": "entry.changed",
                "keys": invalidation_keys,
            }
        }
    )
    return response


@entries_bp.get("/e/entry/<entry_id>/edit")
@login_required
async def entry_edit(entry_id: str):
    _, user, ctx = await require_encryption_context()
    uid = user["id"]
    db = get_services().db
    await ensure_entry_exists(db, uid, entry_id)
    entries = await db.entries.get_entries_by_ids(ctx, [entry_id])
    if not entries:
        abort(404, description="Entry not found.")
    entry = entries[0]
    if entry.get("role") != "user":
        abort(403, description="Only user entries can be edited.")
    today = local_date().isoformat()
    day = entry.get("created_date") or today
    if day != today:
        abort(403, description="Editing is available on the current day only.")
    text_html = entry.get("text_html") or render_markdown_to_html(entry.get("text", ""))
    entry_payload = {
        "id": entry_id,
        "role": entry.get("role"),
        "text": entry.get("text", ""),
        "text_html": text_html,
        "meta": entry.get("meta", {}),
        "created_at": entry.get("created_at"),
    }
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
    await ensure_entry_exists(db, uid, entry_id)
    entries = await db.entries.get_entries_by_ids(ctx, [entry_id])
    if not entries:
        abort(404, description="Entry not found.")
    entry = entries[0]
    tags = []
    if entry.get("role") == "user":
        tags = await db.tags.get_tags_for_entry(ctx, entry_id)
    text_html = entry.get("text_html") or render_markdown_to_html(entry.get("text", ""))
    entry_payload = {
        "id": entry_id,
        "role": entry.get("role"),
        "text": entry.get("text", ""),
        "text_html": text_html,
        "meta": entry.get("meta", {}),
        "tags": tags,
        "created_at": entry.get("created_at"),
    }
    today = local_date().isoformat()
    day = entry.get("created_date") or today
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
    user_text = form.get("text", "").strip()
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
            logger.exception("Failed to parse user_time; falling back to server time")
            created_at = None
            created_date = date
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
    response.headers["HX-Trigger"] = json.dumps(
        {
            "cache:invalidate": {
                "reason": "entry.created",
                "keys": [
                    {
                        "namespace": "summary",
                        "prefix": f"day:{created_date}",
                        "reason": "entry.created",
                    }
                ],
            }
        }
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
