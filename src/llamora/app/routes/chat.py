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
from typing import Any, Mapping

from llamora.llm.chat_template import build_opening_messages, render_chat_prompt

from llamora.app.services.container import get_services
from llamora.app.services.auth_helpers import (
    get_secure_cookie_manager,
    login_required,
)
from llamora.app.services.chat_context import get_chat_context
from llamora.app.services.chat_helpers import (
    StreamSession,
    build_conversation_context,
    locate_message_and_reply,
    normalize_llm_config,
)
from llamora.app.services.time import (
    local_date,
    get_timezone,
    format_date,
    part_of_day,
)
from llamora.app.services.validators import parse_iso_date
from llamora.settings import settings


chat_bp = Blueprint("chat", __name__)


def _db():
    return get_services().db


def _cookies():
    return get_secure_cookie_manager()


def _chat_stream_manager():
    return get_services().llm_service.chat_stream_manager


logger = logging.getLogger(__name__)


async def _require_user() -> Mapping[str, Any]:
    manager = _cookies()
    user = await manager.get_current_user()
    if user is None:
        abort(401)
        raise AssertionError("unreachable")
    return user


def _require_dek() -> bytes:
    manager = _cookies()
    dek = manager.get_dek()
    if dek is None:
        abort(401, description="Missing encryption key")
        raise AssertionError("unreachable")
    return dek


async def render_chat(
    date: str,
    oob: bool = False,
    scroll_target: str | None = None,
    hx_push_url: str | None = None,
) -> Response:
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

    resp = await make_response(html, 200)
    assert isinstance(resp, Response)
    if hx_push_url:
        push_url = hx_push_url
        if scroll_target:
            separator = "&" if "?" in push_url else "?"
            push_url = f"{push_url}{separator}target={scroll_target}"
        resp.headers["HX-Push-Url"] = push_url
    await _db().users.update_state(user["id"], active_date=date)
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
    return await render_chat(
        normalized_date,
        oob=False,
        scroll_target=target,
        hx_push_url=push_url,
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
    )


@chat_bp.route("/c/stop/<user_msg_id>", methods=["POST"])
@login_required
async def stop_generation(user_msg_id: str):
    logger.info("Stop requested for user message %s", user_msg_id)
    manager = _cookies()
    user = await manager.get_current_user()
    dek = manager.get_dek()
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
        opening_messages = build_opening_messages(
            yesterday_messages=yesterday_msgs,
            date=date_str,
            part_of_day=pod,
            is_new=is_new,
            has_no_activity=has_no_activity,
        )
        _ = render_chat_prompt(opening_messages)
    except Exception as exc:
        logger.exception("Failed to prepare opening prompt")

        msg = f"⚠️ {exc}"

        return StreamSession.error(msg)
    stream_id = f"opening:{uid}:{today_iso}"
    manager = _chat_stream_manager()
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

    return StreamSession.pending(pending)


@chat_bp.route("/c/<date>/message", methods=["POST"])
@login_required
async def send_message(date):
    form = await request.form
    user_text = form.get("message", "").strip()
    user_time = form.get("user_time")
    user = await _require_user()
    dek = _require_dek()
    uid = user["id"]

    max_len = int(settings.LIMITS.max_message_length)

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

    return StreamSession.pending(pending_response)
