from quart import (
    Blueprint,
    render_template,
    request,
    Response,
    current_app,
    make_response,
    abort,
    url_for,
)
from html import escape
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from typing import Any

from llm.prompt_template import build_opening_prompt

from app.services.container import get_services
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)
from app.services.chat_context import get_chat_context
from app.services.chat_helpers import (
    build_conversation_context,
    locate_message_and_reply,
    normalize_llm_config,
    replace_newline,
    stream_pending_reply,
    stream_saved_reply,
)
from app.services.time import (
    local_date,
    get_timezone,
    format_date,
    part_of_day,
)
from app.services.validators import parse_iso_date


chat_bp = Blueprint("chat", __name__)


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _db():
    return get_services().db


def _chat_stream_manager():
    return get_services().llm_service.chat_stream_manager


logger = logging.getLogger(__name__)


async def _require_user() -> dict[str, Any]:
    user = await get_current_user()
    if user is None:
        abort(401)
        raise AssertionError("unreachable")
    return user


def _require_dek() -> bytes:
    dek = get_dek()
    if dek is None:
        abort(401, description="Missing encryption key")
        raise AssertionError("unreachable")
    return dek


@dataclass(slots=True)
class ChatRenderResult:
    html: str
    active_date: str
    user_id: str


async def render_chat(
    date: str,
    oob: bool = False,
    scroll_target: str | None = None,
) -> ChatRenderResult:
    user = await _require_user()
    context = await get_chat_context(user, date)
    html = await render_template(
        "partials/chat.html",
        day=date,
        oob=oob,
        user=user,
        scroll_target=scroll_target,
        **context,
    )

    return ChatRenderResult(html=html, active_date=date, user_id=user["id"])


async def _build_chat_response(
    *,
    date: str,
    target: str | None,
    push_url: str,
) -> Response:
    render_result = await render_chat(date, oob=False, scroll_target=target)
    resp = await make_response(render_result.html, 200)
    if target:
        push_url = f"{push_url}?target={target}"
    resp.headers["HX-Push-Url"] = push_url
    await _db().users.update_state(
        render_result.user_id, active_date=render_result.active_date
    )
    return resp


@chat_bp.route("/c/<date>")
@login_required
async def chat_htmx(date):
    try:
        normalized_date = parse_iso_date(date)
    except ValueError as exc:
        abort(400, description="Invalid date")
        raise AssertionError("unreachable") from exc
    target = request.args.get("target")
    push_url = url_for("days.day", date=normalized_date)
    return await _build_chat_response(
        date=normalized_date, target=target, push_url=push_url
    )


@chat_bp.route("/c/today")
@login_required
async def chat_htmx_today():
    target = request.args.get("target")
    date = local_date().isoformat()
    push_url = url_for("days.day_today")
    return await _build_chat_response(date=date, target=target, push_url=push_url)


@chat_bp.route("/c/stop/<user_msg_id>", methods=["POST"])
@login_required
async def stop_generation(user_msg_id: str):
    logger.info("Stop requested for user message %s", user_msg_id)
    user = await get_current_user()
    dek = get_dek()
    if user is None or dek is None:
        logger.debug("Stop request without authenticated user")
        abort(401)
        raise AssertionError("unreachable")
    assert user is not None
    assert dek is not None

    if not await _db().messages.message_exists(user["id"], user_msg_id):
        logger.warning("Stop request for unauthorized message %s", user_msg_id)
        abort(404, description="message not found")

    manager = _chat_stream_manager()
    handled, was_pending = await manager.stop(user_msg_id)
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
    user = await _require_user()
    dek = _require_dek()
    if not await _db().messages.message_exists(user["id"], msg_id):
        abort(404, description="message not found")
    tags = await _db().tags.get_tags_for_message(user["id"], msg_id, dek)
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
    user = await _require_user()
    dek = _require_dek()
    uid = user["id"]
    tz = get_timezone()
    now = datetime.now(ZoneInfo(tz))
    today_iso = now.date().isoformat()
    date_str = format_date(now)
    pod = part_of_day(now)
    yesterday_iso = (now - timedelta(days=1)).date().isoformat()
    is_new = not await _db().messages.user_has_messages(uid)

    yesterday_msgs = await _db().messages.get_recent_history(
        uid, yesterday_iso, dek, limit=20
    )

    has_no_activity = not is_new and not yesterday_msgs
    try:
        prompt = build_opening_prompt(
            yesterday_messages=yesterday_msgs,
            date=date_str,
            part_of_day=pod,
            is_new=is_new,
            has_no_activity=has_no_activity,
        )
    except Exception as exc:
        logger.exception("Failed to build opening prompt")

        async def error_stream():
            msg = f"⚠️ {exc}"
            yield f"event: error\ndata: {replace_newline(escape(msg))}\n\n"
            yield "event: done\ndata: {}\n\n"

        return Response(
            error_stream(),
            mimetype="text/event-stream",
            headers=SSE_HEADERS,
        )
    stream_id = f"opening:{uid}:{today_iso}"
    manager = _chat_stream_manager()
    pending = manager.start_stream(
        stream_id,
        uid,
        today_iso,
        [],
        dek,
        context=None,
        prompt=prompt,
        reply_to=None,
        meta_extra={"auto_opening": True},
    )

    return Response(
        stream_pending_reply(pending),
        mimetype="text/event-stream",
        headers=SSE_HEADERS,
    )


@chat_bp.route("/c/<date>/message", methods=["POST"])
@login_required
async def send_message(date):
    form = await request.form
    user_text = form.get("message", "").strip()
    user_time = form.get("user_time")
    user = await _require_user()
    dek = _require_dek()
    uid = user["id"]

    max_len = current_app.config["MAX_MESSAGE_LENGTH"]

    if not user_text or len(user_text) > max_len:
        abort(400, description="Message is empty or too long.")

    try:
        user_msg_id = await _db().messages.append_message(
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

    try:
        normalized_date = parse_iso_date(date)
    except ValueError as exc:
        abort(400, description="Invalid date")
        raise AssertionError("unreachable") from exc

    user = await _require_user()
    dek = _require_dek()
    uid = user["id"]
    history, existing_assistant_msg, actual_date = await locate_message_and_reply(
        _db(), uid, dek, normalized_date, user_msg_id
    )

    if not history:
        logger.warning("History not found for user message %s", user_msg_id)
        return Response(
            "event: error\ndata: Invalid ID\n\n",
            mimetype="text/event-stream",
            headers=SSE_HEADERS,
        )

    if existing_assistant_msg:
        return Response(
            stream_saved_reply(existing_assistant_msg),
            mimetype="text/event-stream",
            headers=SSE_HEADERS,
        )

    params_raw = normalize_llm_config(
        request.args.get("config"),
        current_app.config.get("ALLOWED_LLM_CONFIG_KEYS", set()),
    )
    params = dict(params_raw) if params_raw is not None else None

    ctx_mapping = build_conversation_context(
        request.args.get("user_time"), request.cookies.get("tz")
    )
    ctx = dict(ctx_mapping)

    manager = _chat_stream_manager()
    pending_response = manager.get(user_msg_id)
    if not pending_response:
        pending_response = manager.start_stream(
            user_msg_id,
            uid,
            actual_date or normalized_date,
            history,
            dek,
            params,
            ctx,
        )

    return Response(
        stream_pending_reply(pending_response),
        mimetype="text/event-stream",
        headers=SSE_HEADERS,
    )
