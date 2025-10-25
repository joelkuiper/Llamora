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
import orjson
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ulid import ULID

from llm.client import LLMClient
from llm.process_manager import LlamafileProcessManager
from llm.prompt_template import build_opening_prompt

from app import db
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)
from app.services.chat_context import get_chat_context
from app.services.chat_stream import ChatStreamManager
from app.services.chat_helpers import (
    build_conversation_context,
    locate_message_and_reply,
    normalize_llm_config,
    stream_pending_reply,
    stream_saved_reply,
)
from app.services.time import (
    local_date,
    get_timezone,
    format_date,
    part_of_day,
)


chat_bp = Blueprint("chat", __name__)


process_manager = LlamafileProcessManager()
llm = LLMClient(process_manager)

chat_stream_manager = ChatStreamManager(llm, db)


logger = logging.getLogger(__name__)


async def render_chat(date, oob=False):
    user = await get_current_user()
    context = await get_chat_context(user, date)
    html = await render_template(
        "partials/chat.html",
        day=date,
        oob=oob,
        user=user,
        **context,
    )

    return html


@chat_bp.route("/c/<date>")
@login_required
async def chat_htmx(date):
    target = request.args.get("target")
    html = await render_chat(date, False)
    resp = await make_response(html, 200)
    push_url = url_for("days.day", date=date)
    if target:
        push_url = f"{push_url}?target={target}"
    resp.headers["HX-Push-Url"] = push_url
    user = await get_current_user()
    await db.users.update_state(user["id"], active_date=date)
    return resp


@chat_bp.route("/c/today")
@login_required
async def chat_htmx_today():
    target = request.args.get("target")
    date = local_date().isoformat()
    html = await render_chat(date, False)
    resp = await make_response(html, 200)
    push_url = url_for("days.day_today")
    if target:
        push_url = f"{push_url}?target={target}"
    resp.headers["HX-Push-Url"] = push_url
    user = await get_current_user()
    await db.users.update_state(user["id"], active_date=date)
    return resp


@chat_bp.route("/c/stop/<user_msg_id>", methods=["POST"])
@login_required
async def stop_generation(user_msg_id: str):
    logger.info("Stop requested for user message %s", user_msg_id)
    handled, was_pending = await chat_stream_manager.stop(user_msg_id)
    if not was_pending:
        logger.debug("No pending response for %s, aborting active stream", user_msg_id)
    if not handled:
        logger.warning("Stop request for unknown user message %s", user_msg_id)
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
    user = await get_current_user()
    dek = get_dek()
    if not await db.messages.message_exists(user["id"], msg_id):
        abort(404, description="message not found")
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
    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()
    tz = get_timezone()
    now = datetime.now(ZoneInfo(tz))
    today_iso = now.date().isoformat()
    date_str = format_date(now)
    pod = part_of_day(now)
    yesterday_iso = (now - timedelta(days=1)).date().isoformat()
    is_new = not await db.messages.user_has_messages(uid)
    yesterday_msgs = await db.messages.get_history(uid, yesterday_iso, dek)
    has_no_activity = not is_new and not yesterday_msgs
    try:
        prompt = build_opening_prompt(
            yesterday_messages=yesterday_msgs[-20:],
            date=date_str,
            part_of_day=pod,
            is_new=is_new,
            has_no_activity=has_no_activity,
        )
    except Exception as e:
        logger.exception("Failed to build opening prompt")

        async def error_stream():
            msg = f"⚠️ {e}"
            yield f"event: error\ndata: {replace_newline(escape(msg))}\n\n"
            yield "event: done\ndata: {}\n\n"

        return Response(error_stream(), mimetype="text/event-stream")
    stream_id = str(ULID())
    pending = chat_stream_manager.start_stream(
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

    async def event_stream():
        async for chunk in pending.stream():
            if pending.error:
                yield f"event: error\ndata: {replace_newline(escape(chunk))}\n\n"
                yield "event: done\ndata: {}\n\n"
                return
            else:
                yield f"event: message\ndata: {replace_newline(escape(chunk))}\n\n"
        data = orjson.dumps({"assistant_msg_id": pending.assistant_msg_id}).decode()
        yield f"event: done\ndata: {data}\n\n"


@chat_bp.route("/c/<date>/message", methods=["POST"])
@login_required
async def send_message(date):
    form = await request.form
    user_text = form.get("message", "").strip()
    user_time = form.get("user_time")
    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()

    max_len = current_app.config["MAX_MESSAGE_LENGTH"]

    if not user_text or len(user_text) > max_len:
        abort(400, description="Message is empty or too long.")

    try:
        user_msg_id = await db.messages.append_message(
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

    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()
    history, existing_assistant_msg, actual_date = await locate_message_and_reply(
        db, uid, dek, date, user_msg_id
    )

    if not history:
        logger.warning("History not found for user message %s", user_msg_id)
        return Response(
            "event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream"
        )

    if existing_assistant_msg:
        return Response(
            stream_saved_reply(existing_assistant_msg),
            mimetype="text/event-stream",
        )

    params = normalize_llm_config(
        request.args.get("config"),
        current_app.config.get("ALLOWED_LLM_CONFIG_KEYS", set()),
    )

    ctx = build_conversation_context(request.args.get("user_time"), request.cookies.get("tz"))

    pending_response = chat_stream_manager.get(user_msg_id)
    if not pending_response:
        pending_response = chat_stream_manager.start_stream(
            user_msg_id,
            uid,
            actual_date or date,
            history,
            dek,
            params,
            ctx,
        )

    return Response(stream_pending_reply(pending_response), mimetype="text/event-stream")
