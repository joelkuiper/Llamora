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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any
from werkzeug.exceptions import HTTPException

from llamora.llm.chat_template import build_opening_messages, render_chat_prompt

from llamora.app.services.container import get_services
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.chat_context import get_chat_context
from llamora.app.services.chat_helpers import (
    augment_history_with_recall,
    apply_reply_kind_prompt,
    history_has_tag_recall,
    StreamSession,
    build_conversation_context,
    normalize_llm_config,
    slice_history_to_entry,
    start_stream_session,
)
from llamora.app.services.entry_context import build_entry_context
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


def _load_reply_kinds() -> tuple[list[dict[str, str]], dict[str, str]]:
    raw = settings.get("LLM.reply_kinds", []) or []
    kinds: list[dict[str, str]] = []
    labels: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind_id = str(entry.get("id") or "").strip()
        label = str(entry.get("label") or "").strip()
        prompt = str(entry.get("prompt") or "").strip()
        if not kind_id or not label:
            continue
        kinds.append({"id": kind_id, "label": label, "prompt": prompt})
        labels[kind_id] = label
    if not kinds:
        kinds = [{"id": "reply", "label": "Reply", "prompt": ""}]
        labels = {"reply": "Reply"}
    return kinds, labels


def _select_reply_kind(kind_id: str | None) -> dict[str, str]:
    kinds, _ = _load_reply_kinds()
    if kind_id:
        match = next((k for k in kinds if k["id"] == kind_id), None)
        if match:
            return match
    return kinds[0]


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
    reply_kinds, reply_kind_labels = _load_reply_kinds()
    html = await render_template(
        "partials/chat.html",
        day=date,
        oob=oob,
        user=user,
        scroll_target=scroll_target,
        view_kind=view_kind,
        reply_kinds=reply_kinds,
        reply_kind_labels=reply_kind_labels,
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
    return Response("", status=200)


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


@chat_bp.route("/c/message/<msg_id>", methods=["DELETE"])
@login_required
async def delete_message(msg_id: str):
    _, user, _ = await require_user_and_dek()
    db = get_services().db
    await ensure_message_exists(db, user["id"], msg_id)
    deleted_ids = await db.messages.delete_message(user["id"], msg_id)
    if deleted_ids:
        await get_services().search_api.delete_messages(user["id"], deleted_ids)
    oob_deletes = "\n".join(
        f'<div id="msg-{mid}" hx-swap-oob="delete"></div>' for mid in deleted_ids
    )
    return Response(oob_deletes, status=200, mimetype="text/html")


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
            use_default_reply_to=False,
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
    reply_kinds, reply_kind_labels = _load_reply_kinds()

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

    created_at = user_time or datetime.now(timezone.utc).isoformat()
    return await render_template(
        "partials/placeholder.html",
        user_text=user_text,
        user_msg_id=user_msg_id,
        created_at=created_at,
        day=date,
        user_time=user_time,
        reply_kinds=reply_kinds,
        reply_kind_labels=reply_kind_labels,
        is_today=date == local_date().isoformat(),
    )


@chat_bp.route("/c/<date>/ask/<user_msg_id>", methods=["POST"])
@login_required
async def ask_llm(date, user_msg_id: str):
    normalized_date = require_iso_date(date)
    form = await request.form
    user_time = form.get("user_time")
    reply_kind = form.get("reply_kind") or request.args.get("reply_kind")
    selected_kind = _select_reply_kind(reply_kind)
    _, user, dek = await require_user_and_dek()
    uid = user["id"]

    await ensure_message_exists(get_services().db, uid, user_msg_id)
    actual_date = await get_services().db.messages.get_message_date(uid, user_msg_id)
    if actual_date is None:
        abort(404, description="Message not found.")

    return await render_template(
        "partials/assistant_stream_item.html",
        user_msg_id=user_msg_id,
        day=actual_date or normalized_date,
        user_time=user_time,
        reply_kind=selected_kind.get("id"),
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
    actual_date = await get_services().db.messages.get_message_date(uid, user_msg_id)
    if not actual_date:
        logger.warning("History not found for user message %s", user_msg_id)
        return StreamSession.error("Invalid ID")
    history = await get_services().db.messages.get_history(uid, actual_date, dek)
    if not history:
        logger.warning("History not found for user message %s", user_msg_id)
        return StreamSession.error("Invalid ID")
    history = slice_history_to_entry(history, user_msg_id)

    params_raw = normalize_llm_config(
        request.args.get("config"),
        set(settings.LLM.allowed_config_keys),
    )
    params = dict(params_raw) if params_raw is not None else None

    reply_kind = request.args.get("reply_kind")
    selected_kind = _select_reply_kind(reply_kind)
    ctx_mapping = build_conversation_context(
        request.args.get("user_time"), request.cookies.get("tz")
    )
    ctx = dict(ctx_mapping)

    services = get_services()
    db = services.db
    manager = services.llm_service.chat_stream_manager
    entry_context = await build_entry_context(
        db,
        uid,
        dek,
        user_msg_id=user_msg_id,
    )
    if entry_context:
        ctx.update(entry_context)

    try:
        pending_response = manager.get(user_msg_id, uid)
        if not pending_response:
            recall_context = await build_tag_recall_context(
                db,
                uid,
                dek,
                history=history,
                current_date=actual_date or normalized_date,
                max_message_id=user_msg_id,
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
                message_key="message",
                target_message_id=user_msg_id,
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
            history_for_stream = apply_reply_kind_prompt(
                history_for_stream, selected_kind.get("prompt")
            )
            try:
                pending_response = await start_stream_session(
                    manager=manager,
                    user_msg_id=user_msg_id,
                    uid=uid,
                    date=actual_date or normalized_date,
                    history=history_for_stream,
                    dek=dek,
                    params=params,
                    context=ctx,
                    meta_extra={
                        "tag_recall_applied": recall_applied,
                        "reply_kind": selected_kind.get("id"),
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
        logger.exception("Failed to start streaming reply for %s", user_msg_id)
        return StreamSession.error(
            "The assistant ran into an unexpected error. Please try again."
        )
