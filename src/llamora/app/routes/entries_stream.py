"""SSE streaming endpoints for diary entries.

This module handles the Server-Sent Events (SSE) streaming for:
- Opening messages (daily greeting from assistant)
- Response streaming (assistant replies to user entries)
- Stream stop/cancellation
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from quart import Blueprint, Response, request
from werkzeug.exceptions import HTTPException

from llamora.app.routes.helpers import (
    ensure_entry_exists,
    require_iso_date,
    require_user_and_dek,
)
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.container import get_services
from llamora.app.services.entry_context import build_entry_context, build_llm_context
from llamora.app.services.entry_helpers import (
    StreamSession,
    augment_history_with_recall,
    augment_opening_with_recall,
    build_entry_history,
    history_has_tag_recall,
    normalize_llm_config,
    start_stream_session,
)
from llamora.app.services.response_stream.manager import StreamCapacityError
from llamora.app.services.tag_recall import build_tag_recall_context
from llamora.app.services.time import get_timezone
from llamora.llm.entry_template import (
    build_opening_messages,
    estimate_entry_messages_tokens,
)
from llamora.settings import settings

entries_stream_bp = Blueprint("entries_stream", __name__)

logger = logging.getLogger(__name__)


def _entry_stream_manager():
    return get_services().llm_service.response_stream_manager


@entries_stream_bp.route("/e/response/stop/<entry_id>", methods=["POST"])
@login_required
async def stop_response(entry_id: str):
    """Stop an in-progress response stream."""
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


@entries_stream_bp.get("/e/opening/<date>")
@login_required
async def sse_opening(date: str):
    """Stream the daily opening message from the assistant."""
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    tz = get_timezone()
    normalized_date = require_iso_date(date)
    target_date = datetime.fromisoformat(normalized_date).date()
    now = datetime.now(ZoneInfo(tz))
    target_dt = datetime.combine(target_date, now.timetz())
    today_iso = target_date.isoformat()
    ctx = build_llm_context(
        user_time=target_dt.isoformat(),
        tz_cookie=tz,
    )
    date_str = str(ctx.get("date") or "")
    pod = str(ctx.get("part_of_day") or "")
    yesterday_iso = (target_date - timedelta(days=1)).isoformat()
    services = get_services()
    db = services.db
    is_new = not await db.entries.user_has_entries(uid)

    yesterday_msgs = await db.entries.get_recent_entries(uid, yesterday_iso, dek, limit=20)
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
            llm=llm_client,
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
        prompt_tokens = estimate_entry_messages_tokens(opening_messages)
        snapshot = budget.diagnostics(
            prompt_tokens=prompt_tokens,
            label="entry:opening",
            extra={
                "phase": "initial",
                "messages": len(opening_messages),
                "recall_inserted": recall_inserted,
            },
        )
        max_tokens = snapshot.max_tokens
        if max_tokens is not None and prompt_tokens > max_tokens:
            if recall_inserted:
                logger.info(
                    "Dropping tag recall context from opening prompt due to budget (%s > %s)",
                    prompt_tokens,
                    max_tokens,
                )
                drop_index = recall_index if recall_index is not None else 1
                if 0 <= drop_index < len(opening_messages):
                    opening_messages.pop(drop_index)
                prompt_tokens = estimate_entry_messages_tokens(opening_messages)
                budget.diagnostics(
                    prompt_tokens=prompt_tokens,
                    label="entry:opening",
                    extra={
                        "phase": "after-recall-drop",
                        "messages": len(opening_messages),
                        "recall_inserted": False,
                    },
                )
            if prompt_tokens > max_tokens:
                budget.diagnostics(
                    prompt_tokens=prompt_tokens,
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
            created_at=target_dt.isoformat(),
            use_default_reply_to=False,
        )
    except StreamCapacityError as exc:
        return StreamSession.backpressure(
            "The assistant is busy. Please try again in a moment.",
            exc.retry_after,
        )

    return StreamSession.pending(pending)


@entries_stream_bp.route("/e/<date>/response/stream/<entry_id>")
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
    entries = await get_services().db.entries.get_entries_for_date(uid, actual_date, dek)
    if not entries:
        logger.warning("Entries not found for entry %s", entry_id)
        return StreamSession.error("Invalid ID")
    history = build_entry_history(entries, entry_id)

    params_raw = normalize_llm_config(
        request.args.get("config"),
        set(settings.LLM.allowed_config_keys),
    )
    params = dict(params_raw) if params_raw is not None else None

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
            llm_client = services.llm_service.llm
            recall_context = await build_tag_recall_context(
                db,
                uid,
                dek,
                history=history,
                current_date=actual_date or normalized_date,
                llm=llm_client,
                max_entry_id=entry_id,
                target_entry_id=entry_id,
            )
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
                    },
                    created_at=request.args.get("user_time"),
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


__all__ = [
    "entries_stream_bp",
    "stop_response",
    "sse_opening",
    "sse_response",
]
