from __future__ import annotations

from quart import (
    Blueprint,
    render_template,
    request,
    Response,
    make_response,
    abort,
    url_for,
)
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any
from werkzeug.exceptions import HTTPException

from llamora.llm.chat_template import build_opening_messages, render_chat_prompt

from llamora.app.services.container import get_services
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.chat_context import get_chat_context
from llamora.app.services.chat_helpers import (
    augment_history_with_recall,
    StreamSession,
    build_conversation_context,
    locate_message_and_reply,
    normalize_llm_config,
)
from llamora.app.services.chat_stream.manager import StreamCapacityError
from llamora.app.services.tag_recall import build_tag_recall_context
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import (
    local_date,
    get_timezone,
    format_date,
    part_of_day,
)
from llamora.app.routes.helpers import (
    ensure_message_exists,
    require_iso_date,
    require_user_and_dek,
)
from llamora.settings import settings


chat_bp = Blueprint("chat", __name__)


def _chat_stream_manager():
    return get_services().llm_service.chat_stream_manager


logger = logging.getLogger(__name__)


async def render_chat(
    date: str,
    *,
    oob: bool = False,
    scroll_target: str | None = None,
    hx_push_url: str | None = None,
    view_kind: str = "day",
) -> Response:
    session = get_session_context()
    user = await session.require_user()
    context = await get_chat_context(user, date)
    html = await render_template(
        "partials/chat.html",
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


@chat_bp.route("/c/<date>")
@login_required
async def chat_htmx(date):
    normalized_date = require_iso_date(date)
    target = request.args.get("target")
    push_url = url_for("days.day", date=normalized_date)
    return await render_chat(
        normalized_date,
        oob=False,
        scroll_target=target,
        hx_push_url=push_url,
        view_kind="day",
    )


@chat_bp.route("/c/today")
@login_required
async def chat_htmx_today():
    target = request.args.get("target")
    date = local_date().isoformat()
    push_url = url_for("days.day_today")
    return await render_chat(
        date,
        oob=False,
        scroll_target=target,
        hx_push_url=push_url,
        view_kind="today",
    )


@chat_bp.route("/c/stop/<user_msg_id>", methods=["POST"])
@login_required
async def stop_generation(user_msg_id: str):
    logger.info("Stop requested for user message %s", user_msg_id)
    _, user, _ = await require_user_and_dek()

    try:
        await ensure_message_exists(get_services().db, user["id"], user_msg_id)
    except HTTPException as exc:
        if exc.code == 404:
            logger.warning("Stop request for unauthorized message %s", user_msg_id)
        raise

    manager = _chat_stream_manager()
    handled, was_pending = await manager.stop(user_msg_id, user["id"])
    if not was_pending:
        logger.debug("No pending response for %s, aborting active stream", user_msg_id)
    if not handled:
        logger.debug("Stop request for %s not handled by manager", user_msg_id)
        return Response("unknown message id", status=404)
    logger.debug(
        "Stop request handled for %s (pending=%s)",
        user_msg_id,
        was_pending,
    )
    return Response(status=204)


@chat_bp.get("/c/meta-chips/<msg_id>")
@login_required
async def meta_chips(msg_id: str):
    _, user, dek = await require_user_and_dek()
    db = get_services().db
    await ensure_message_exists(db, user["id"], msg_id)
    tags = await db.tags.get_tags_for_message(user["id"], msg_id, dek)
    html = await render_template(
        "partials/meta_chips_wrapper.html",
        msg_id=msg_id,
        tags=tags,
        hidden=True,
    )
    return html


@chat_bp.get("/c/opening/<date>")
@login_required
async def sse_opening(date: str):
    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    tz = get_timezone()
    now = datetime.now(ZoneInfo(tz))
    today_iso = now.date().isoformat()
    date_str = format_date(now)
    pod = part_of_day(now)
    yesterday_iso = (now - timedelta(days=1)).date().isoformat()
    services = get_services()
    db = services.db
    is_new = not await db.messages.user_has_messages(uid)

    yesterday_msgs = await db.messages.get_recent_history(
        uid, yesterday_iso, dek, limit=20
    )
    had_yesterday_activity = bool(yesterday_msgs)

    trim_context = {"date": date_str, "part_of_day": pod}
    llm_client = services.llm_service.llm

    try:
        yesterday_msgs = await llm_client.trim_history(
            yesterday_msgs,
            context=trim_context,
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
        augmentation = await augment_history_with_recall(
            opening_messages,
            recall_context,
            llm_client=None,
            message_key="content",
            insert_index=1,
        )
        opening_messages = augmentation.messages
        recall_inserted = augmentation.recall_inserted
        recall_index = augmentation.recall_index

        budget = llm_client.prompt_budget
        prompt_render = render_chat_prompt(opening_messages)
        snapshot = budget.diagnostics(
            prompt_tokens=prompt_render.token_count,
            label="chat:opening",
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
                prompt_render = render_chat_prompt(opening_messages)
                budget.diagnostics(
                    prompt_tokens=prompt_render.token_count,
                    label="chat:opening",
                    extra={
                        "phase": "after-recall-drop",
                        "messages": len(opening_messages),
                        "recall_inserted": False,
                    },
                )
            if prompt_render.token_count > max_tokens:
                budget.diagnostics(
                    prompt_tokens=prompt_render.token_count,
                    label="chat:opening",
                    extra={
                        "phase": "overflow-after-trim",
                        "messages": len(opening_messages),
                        "recall_inserted": False,
                    },
                )
                raise ValueError("Opening prompt exceeds context window")
    except Exception as exc:
        logger.exception("Failed to prepare opening prompt")

        msg = f"⚠️ {exc}"

        return StreamSession.error(msg)
    stream_id = f"opening:{uid}:{today_iso}"
    manager = _chat_stream_manager()
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
        )
    except StreamCapacityError as exc:
        return StreamSession.backpressure(
            "The assistant is busy. Please try again in a moment.",
            exc.retry_after,
        )

    return StreamSession.pending(pending)


@chat_bp.route("/c/<date>/message", methods=["POST"])
@login_required
async def send_message(date):
    form = await request.form
    user_text = form.get("message", "").strip()
    user_time = form.get("user_time")
    _, user, dek = await require_user_and_dek()
    uid = user["id"]

    max_len = int(settings.LIMITS.max_message_length)

    if not user_text or len(user_text) > max_len:
        abort(400, description="Message is empty or too long.")

    try:
        user_msg_id = await get_services().db.messages.append_message(
            uid, "user", user_text, dek, created_date=date
        )
        logger.debug("Saved user message %s", user_msg_id)
    except Exception:
        logger.exception("Failed to save user message")
        raise

    return await render_template(
        "partials/placeholder.html",
        user_text=user_text,
        user_msg_id=user_msg_id,
        day=date,
        user_time=user_time,
    )


@chat_bp.route("/c/<date>/stream/<user_msg_id>")
@login_required
async def sse_reply(user_msg_id: str, date: str):
    """Stream the assistant's reply for a given user message.

    The ``user_msg_id`` corresponds to the user's prompt message. When the
    assistant finishes responding, the ``assistant_msg_id`` of the stored reply
    is sent in a final ``done`` event.
    """

    normalized_date = require_iso_date(date)

    _, user, dek = await require_user_and_dek()
    uid = user["id"]
    history, existing_assistant_msg, actual_date = await locate_message_and_reply(
        get_services().db, uid, dek, normalized_date, user_msg_id
    )

    if not history:
        logger.warning("History not found for user message %s", user_msg_id)
        return StreamSession.error("Invalid ID")

    if existing_assistant_msg:
        return StreamSession.saved(existing_assistant_msg)

    params_raw = normalize_llm_config(
        request.args.get("config"),
        set(settings.LLM.allowed_config_keys),
    )
    params = dict(params_raw) if params_raw is not None else None

    ctx_mapping = build_conversation_context(
        request.args.get("user_time"), request.cookies.get("tz")
    )
    ctx = dict(ctx_mapping)

    services = get_services()
    db = services.db
    manager = services.llm_service.chat_stream_manager

    try:
        pending_response = manager.get(user_msg_id, uid)
        if not pending_response:
            recall_context = await build_tag_recall_context(
                db,
                uid,
                dek,
                history=history,
                current_date=actual_date or normalized_date,
            )
            llm_client = services.llm_service.llm
            augmentation = await augment_history_with_recall(
                history,
                recall_context,
                llm_client=llm_client,
                params=params,
                context=ctx,
                message_key="message",
                target_message_id=user_msg_id,
                include_tag_metadata=True,
            )
            history_for_stream = augmentation.messages
            try:
                pending_response = manager.start_stream(
                    user_msg_id,
                    uid,
                    actual_date or normalized_date,
                    history_for_stream,
                    dek,
                    params,
                    ctx,
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
        logger.exception("Failed to start streaming reply for %s", user_msg_id)
        return StreamSession.error(
            "The assistant ran into an unexpected error. Please try again."
        )
