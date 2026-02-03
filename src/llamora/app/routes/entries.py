from __future__ import annotations

import asyncio
from quart import (
    Blueprint,
    render_template,
    request,
    Response,
    make_response,
    abort,
    url_for,
)
import orjson
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from werkzeug.exceptions import HTTPException

from llamora.llm.entry_template import build_opening_messages, render_entry_prompt

from llamora.app.services.container import get_services
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.entry_context import (
    get_entries_context,
    build_entry_context,
    build_llm_context,
)
from llamora.app.services.entry_helpers import (
    augment_history_with_recall,
    augment_opening_with_recall,
    apply_response_kind_prompt,
    history_has_tag_recall,
    StreamSession,
    build_entry_history,
    normalize_llm_config,
    start_stream_session,
)
from llamora.app.services.response_stream.manager import StreamCapacityError
from llamora.app.services.tag_recall import build_tag_recall_context
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import (
    local_date,
    get_timezone,
)
from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.routes.helpers import (
    ensure_entry_exists,
    require_iso_date,
    require_user_and_dek,
)
from llamora.settings import settings


entries_bp = Blueprint("entries", __name__)


def _entry_stream_manager():
    return get_services().llm_service.response_stream_manager


def _load_response_kinds() -> list[dict[str, str]]:
    raw = settings.get("LLM.response_kinds", []) or []
    kinds: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind_id = str(entry.get("id") or "").strip()
        label = str(entry.get("label") or "").strip()
        prompt = str(entry.get("prompt") or "").strip()
        if not kind_id or not label:
            continue
        kinds.append({"id": kind_id, "label": label, "prompt": prompt})
    if not kinds:
        kinds = [{"id": "reply", "label": "Reply", "prompt": ""}]
    return kinds


def _select_response_kind(kind_id: str | None) -> dict[str, str]:
    kinds = _load_response_kinds()
    if kind_id:
        match = next((k for k in kinds if k["id"] == kind_id), None)
        if match:
            return match
    return kinds[0]


logger = logging.getLogger(__name__)


async def render_entries(
    date: str,
    *,
    oob: bool = False,
    scroll_target: str | None = None,
    hx_push_url: str | None = None,
    view_kind: str = "day",
) -> Response:
    session = get_session_context()
    user = await session.require_user()
    context = await get_entries_context(user, date)
    response_kinds = _load_response_kinds()
    html = await render_template(
        "partials/entries.html",
        day=date,
        oob=oob,
        user=user,
        scroll_target=scroll_target,
        view_kind=view_kind,
        response_kinds=response_kinds,
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


@entries_bp.route("/e/response/stop/<entry_id>", methods=["POST"])
@login_required
async def stop_response(entry_id: str):
    logger.info("Stop requested for entry %s", entry_id)
    _, user, _ = await require_user_and_dek()

    try:
        await ensure_entry_exists(get_services().db, user["id"], entry_id)
    except HTTPException as exc:
        if exc.code == 404:
            logger.warning("Stop request for unauthorized entry %s", entry_id)
        raise

    manager = _entry_stream_manager()
    handled, was_pending = await manager.stop(entry_id, user["id"])
    if not was_pending:
        logger.debug("No pending response for %s, aborting active stream", entry_id)
    if not handled:
        logger.debug("Stop request for %s not handled by manager", entry_id)
        return Response("unknown entry id", status=404)
    logger.debug(
        "Stop request handled for %s (pending=%s)",
        entry_id,
        was_pending,
    )
    return Response("", status=200)


@entries_bp.get("/e/entry-tags/<entry_id>")
@login_required
async def entry_tags(entry_id: str):
    _, user, dek = await require_user_and_dek()
    db = get_services().db
    await ensure_entry_exists(db, user["id"], entry_id)
    tags = await db.tags.get_tags_for_entry(user["id"], entry_id, dek)
    html = await render_template(
        "partials/entry_tags_wrapper.html",
        entry_id=entry_id,
        tags=tags,
        hidden=True,
    )
    return html


@entries_bp.route("/e/entry/<entry_id>", methods=["DELETE"])
@login_required
async def delete_entry(entry_id: str):
    _, user, _ = await require_user_and_dek()
    db = get_services().db
    await ensure_entry_exists(db, user["id"], entry_id)
    deleted_ids = await db.entries.delete_entry(user["id"], entry_id)
    if deleted_ids:
        await get_services().search_api.delete_entries(user["id"], deleted_ids)
    oob_deletes = "\n".join(
        f'<div id="{target_id}" hx-swap-oob="delete"></div>'
        for mid in deleted_ids
        for target_id in (f"entry-{mid}", f"entry-responses-{mid}")
    )
    return Response(oob_deletes, status=200, mimetype="text/html")


@entries_bp.route("/e/entry/<entry_id>", methods=["PUT", "PATCH"])
@login_required
async def update_entry(entry_id: str):
    form = await request.form
    text = form.get("text", "")
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    db = get_services().db

    max_len = int(settings.LIMITS.max_message_length)
    if not text.strip() or len(text) > max_len:
        abort(400, description="Entry is empty or too long.")

    await ensure_entry_exists(db, uid, entry_id)

    entries = await db.entries.get_entries_by_ids(uid, [entry_id], dek)
    if not entries:
        abort(404, description="Entry not found.")
    current = entries[0]
    if current.get("role") != "user":
        abort(403, description="Only user entries can be edited.")

    updated = await db.entries.update_entry_text(
        uid, entry_id, text, dek, meta=current.get("meta", {})
    )
    if not updated:
        abort(404, description="Entry not found.")

    try:
        record_plain = orjson.dumps(
            {"text": text, "meta": updated.get("meta", {})}
        ).decode()
        await get_services().search_api.delete_entries(uid, [entry_id])
        await get_services().search_api.enqueue_index_job(
            uid, entry_id, record_plain, dek
        )
    except Exception:
        logger.exception("Failed to update search index for entry %s", entry_id)

    tags = await db.tags.get_tags_for_entry(uid, entry_id, dek)
    entry_payload = {
        "id": entry_id,
        "role": updated.get("role"),
        "text": updated.get("text", ""),
        "text_html": render_markdown_to_html(updated.get("text", "")),
        "meta": updated.get("meta", {}),
        "tags": tags,
        "created_at": updated.get("created_at"),
    }
    day = updated.get("created_date") or local_date().isoformat()
    response_kinds = _load_response_kinds()

    return await render_template(
        "partials/entry_main_only.html",
        entry=entry_payload,
        day=day,
        is_today=day == local_date().isoformat(),
    )


@entries_bp.get("/e/entry/<entry_id>/edit")
@login_required
async def entry_edit(entry_id: str):
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    db = get_services().db
    await ensure_entry_exists(db, uid, entry_id)
    entries = await db.entries.get_entries_by_ids(uid, [entry_id], dek)
    if not entries:
        abort(404, description="Entry not found.")
    entry = entries[0]
    if entry.get("role") != "user":
        abort(403, description="Only user entries can be edited.")
    day = entry.get("created_date") or local_date().isoformat()
    if day != local_date().isoformat():
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
        "partials/entry_edit_main_only.html",
        entry=entry_payload,
        day=day,
        is_today=True,
    )


@entries_bp.get("/e/entry/<entry_id>/main")
@login_required
async def entry_main(entry_id: str):
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    db = get_services().db
    await ensure_entry_exists(db, uid, entry_id)
    entries = await db.entries.get_entries_by_ids(uid, [entry_id], dek)
    if not entries:
        abort(404, description="Entry not found.")
    entry = entries[0]
    tags = []
    if entry.get("role") == "user":
        tags = await db.tags.get_tags_for_entry(uid, entry_id, dek)
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
    day = entry.get("created_date") or local_date().isoformat()
    return await render_template(
        "partials/entry_main_only.html",
        entry=entry_payload,
        day=day,
        is_today=day == local_date().isoformat(),
    )


@entries_bp.get("/e/opening/<date>")
@login_required
async def sse_opening(date: str):
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    tz = get_timezone()
    now = datetime.now(ZoneInfo(tz))
    today_iso = now.date().isoformat()
    ctx = build_llm_context(
        user_time=now.isoformat(),
        tz_cookie=tz,
    )
    date_str = str(ctx.get("date") or "")
    pod = str(ctx.get("part_of_day") or "")
    yesterday_iso = (now - timedelta(days=1)).date().isoformat()
    services = get_services()
    db = services.db
    is_new = not await db.entries.user_has_entries(uid)

    yesterday_msgs = await db.entries.get_recent_entries(
        uid, yesterday_iso, dek, limit=20
    )
    had_yesterday_activity = bool(yesterday_msgs)

    llm_client = services.llm_service.llm

    try:
        yesterday_msgs = await llm_client.trim_history(
            yesterday_msgs,
            context=ctx,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to trim opening history")

    has_no_activity = not is_new and not had_yesterday_activity
    try:
        opening_messages = build_opening_messages(
            yesterday_messages=yesterday_msgs,
            date=date_str,
            part_of_day=pod,
            is_new=is_new,
            has_no_activity=has_no_activity,
        )
        recall_context = await build_tag_recall_context(
            db,
            uid,
            dek,
            history=yesterday_msgs,
            current_date=today_iso,
        )
        augmentation = await augment_opening_with_recall(
            opening_messages,
            recall_context,
            llm_client=None,
            insert_index=1,
        )
        opening_messages = augmentation.messages
        recall_inserted = augmentation.recall_inserted
        recall_index = augmentation.recall_index

        budget = llm_client.prompt_budget
        prompt_render = render_entry_prompt(opening_messages)
        snapshot = budget.diagnostics(
            prompt_tokens=prompt_render.token_count,
            label="entry:opening",
            extra={
                "phase": "initial",
                "messages": len(opening_messages),
                "recall_inserted": recall_inserted,
            },
        )
        max_tokens = snapshot.max_tokens
        if max_tokens is not None and prompt_render.token_count > max_tokens:
            if recall_inserted:
                logger.info(
                    "Dropping tag recall context from opening prompt due to budget (%s > %s)",
                    prompt_render.token_count,
                    max_tokens,
                )
                drop_index = recall_index if recall_index is not None else 1
                if 0 <= drop_index < len(opening_messages):
                    opening_messages.pop(drop_index)
                prompt_render = render_entry_prompt(opening_messages)
                budget.diagnostics(
                    prompt_tokens=prompt_render.token_count,
                    label="entry:opening",
                    extra={
                        "phase": "after-recall-drop",
                        "messages": len(opening_messages),
                        "recall_inserted": False,
                    },
                )
            if prompt_render.token_count > max_tokens:
                budget.diagnostics(
                    prompt_tokens=prompt_render.token_count,
                    label="entry:opening",
                    extra={
                        "phase": "overflow-after-trim",
                        "messages": len(opening_messages),
                        "recall_inserted": False,
                    },
                )
                raise ValueError("Opening prompt exceeds context window")
    except Exception as exc:
        logger.exception("Failed to prepare opening prompt")

        error_text = f"⚠️ {exc}"

        return StreamSession.error(error_text)
    stream_id = f"opening:{uid}:{today_iso}"
    manager = _entry_stream_manager()
    try:
        pending = manager.start_stream(
            stream_id,
            uid,
            today_iso,
            [],
            dek,
            context=None,
            messages=opening_messages,
            reply_to=None,
            meta_extra={"auto_opening": True},
            use_default_reply_to=False,
        )
    except StreamCapacityError as exc:
        return StreamSession.backpressure(
            "The assistant is busy. Please try again in a moment.",
            exc.retry_after,
        )

    return StreamSession.pending(pending)


@entries_bp.route("/e/<date>/entry", methods=["POST"])
@login_required
async def send_entry(date):
    form = await request.form
    user_text = form.get("text", "").strip()
    user_time = form.get("user_time")
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    response_kinds = _load_response_kinds()

    max_len = int(settings.LIMITS.max_message_length)

    if not user_text or len(user_text) > max_len:
        abort(400, description="Entry is empty or too long.")

    try:
        entry_id = await get_services().db.entries.append_entry(
            uid, "user", user_text, dek, created_date=date
        )
        logger.debug("Saved entry %s", entry_id)
    except Exception:
        logger.exception("Failed to save entry")
        raise

    created_at = user_time or datetime.now(timezone.utc).isoformat()
    entry_payload = {
        "id": entry_id,
        "role": "user",
        "text": user_text,
        "text_html": render_markdown_to_html(user_text),
        "meta": {},
        "tags": [],
        "created_at": created_at,
    }
    return await render_template(
        "partials/entries_list.html",
        entries=[{"entry": entry_payload, "responses": []}],
        day=date,
        response_kinds=response_kinds,
        is_today=date == local_date().isoformat(),
    )


@entries_bp.route("/e/<date>/response/<entry_id>", methods=["POST"])
@login_required
async def request_response(date, entry_id: str):
    normalized_date = require_iso_date(date)
    form = await request.form
    user_time = form.get("user_time")
    response_kind = form.get("response_kind") or request.args.get("response_kind")
    selected_kind = _select_response_kind(response_kind)
    response_kinds = _load_response_kinds()
    _, user, dek = await require_user_and_dek()
    uid = user["id"]

    await ensure_entry_exists(get_services().db, uid, entry_id)
    actual_date = await get_services().db.entries.get_entry_date(uid, entry_id)
    if actual_date is None:
        abort(404, description="Entry not found.")

    stream_html = await render_template(
        "partials/entry_response_stream_item.html",
        entry_id=entry_id,
        day=actual_date or normalized_date,
        user_time=user_time,
        response_kind=selected_kind.get("id"),
        response_kinds=response_kinds,
    )
    actions_html = await render_template(
        "partials/entry_actions_item.html",
        entry_id=entry_id,
        day=actual_date or normalized_date,
        response_kinds=response_kinds,
        is_today=normalized_date == local_date().isoformat(),
        stop_url=url_for("entries.stop_response", entry_id=entry_id),
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
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    await ensure_entry_exists(get_services().db, uid, entry_id)
    actual_date = await get_services().db.entries.get_entry_date(uid, entry_id)
    if actual_date is None:
        abort(404, description="Entry not found.")
    response_kinds = _load_response_kinds()
    html = await render_template(
        "partials/entry_actions_item.html",
        entry_id=entry_id,
        day=actual_date,
        response_kinds=response_kinds,
        is_today=actual_date == local_date().isoformat(),
        stop_url=None,
        response_active=False,
    )
    return Response(html, status=200, mimetype="text/html")


@entries_bp.route("/e/<date>/response/stream/<entry_id>")
@login_required
async def sse_response(entry_id: str, date: str):
    """Stream the assistant's response for a given entry.

    The ``entry_id`` corresponds to the user's prompt entry. When the
    assistant finishes responding, the ``assistant_entry_id`` of the stored response
    is sent in a final ``done`` event.
    """

    normalized_date = require_iso_date(date)

    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    actual_date = await get_services().db.entries.get_entry_date(uid, entry_id)
    if not actual_date:
        logger.warning("Entry date not found for entry %s", entry_id)
        return StreamSession.error("Invalid ID")
    entries = await get_services().db.entries.get_entries_for_date(
        uid, actual_date, dek
    )
    if not entries:
        logger.warning("Entries not found for entry %s", entry_id)
        return StreamSession.error("Invalid ID")
    history = build_entry_history(entries, entry_id)

    params_raw = normalize_llm_config(
        request.args.get("config"),
        set(settings.LLM.allowed_config_keys),
    )
    params = dict(params_raw) if params_raw is not None else None

    response_kind = request.args.get("response_kind")
    selected_kind = _select_response_kind(response_kind)
    services = get_services()
    db = services.db
    manager = services.llm_service.response_stream_manager
    entry_context = await build_entry_context(
        db,
        uid,
        dek,
        entry_id=entry_id,
    )
    ctx = build_llm_context(
        user_time=request.args.get("user_time"),
        tz_cookie=request.cookies.get("tz"),
        entry_context=entry_context,
    )

    try:
        pending_response = manager.get(entry_id, uid)
        if not pending_response:
            recall_context = await build_tag_recall_context(
                db,
                uid,
                dek,
                history=history,
                current_date=actual_date or normalized_date,
                max_entry_id=entry_id,
            )
            llm_client = services.llm_service.llm
            recall_date = actual_date or normalized_date
            recall_tags = tuple(recall_context.tags) if recall_context else ()
            has_existing_guidance = False
            if recall_context and recall_tags:
                has_existing_guidance = history_has_tag_recall(
                    history,
                    tags=recall_tags,
                    date=recall_date,
                )
            augmentation = await augment_history_with_recall(
                history,
                None if has_existing_guidance else recall_context,
                llm_client=llm_client,
                params=params,
                context=ctx,
                target_entry_id=entry_id,
                include_tag_metadata=True,
                tag_recall_date=recall_date,
            )
            history_for_stream = augmentation.messages
            recall_applied = False
            if recall_context and recall_tags:
                recall_applied = history_has_tag_recall(
                    history_for_stream,
                    tags=recall_tags,
                    date=recall_date,
                )
            else:
                recall_applied = False
            history_for_stream = apply_response_kind_prompt(
                history_for_stream, selected_kind.get("prompt")
            )
            try:
                pending_response = await start_stream_session(
                    manager=manager,
                    entry_id=entry_id,
                    uid=uid,
                    date=actual_date or normalized_date,
                    history=history_for_stream,
                    dek=dek,
                    params=params,
                    context=ctx,
                    meta_extra={
                        "tag_recall_applied": recall_applied,
                        "response_kind": selected_kind.get("id"),
                    },
                )
            except StreamCapacityError as exc:
                return StreamSession.backpressure(
                    "The assistant is busy. Please try again in a moment.",
                    exc.retry_after,
                )
        return StreamSession.pending(pending_response)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to start streaming response for %s", entry_id)
        return StreamSession.error(
            "The assistant ran into an unexpected error. Please try again."
        )
