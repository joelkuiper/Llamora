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
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ulid import ULID

from llm.llm_engine import LLMEngine
from llm.prompt_template import build_opening_prompt

from app import db
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)
from app.services.chat_stream import ChatStreamManager
from app.services.time import (
    local_date,
    date_and_part,
    get_timezone,
    format_date,
    part_of_day,
)


chat_bp = Blueprint("chat", __name__)


llm = LLMEngine()
chat_stream_manager = ChatStreamManager(llm, db)


logger = logging.getLogger(__name__)


async def render_chat(date, oob=False):
    user = await get_current_user()
    uid = user["id"]

    dek = get_dek()
    history = await db.get_history(uid, date, dek)
    today = local_date().isoformat()
    opening_stream = False
    if not history and date == today:
        opening_stream = True
    pending_msg_id = None
    if history and history[-1]["role"] == "user":
        pending_msg_id = history[-1]["id"]
    html = await render_template(
        "partials/chat.html",
        day=date,
        history=history,
        oob=oob,
        pending_msg_id=pending_msg_id,
        user=user,
        is_today=(date == today),
        opening_stream=opening_stream,
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
    await db.update_state(user["id"], active_date=date)
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
    await db.update_state(user["id"], active_date=date)
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
    if not await db.message_exists(user["id"], msg_id):
        abort(404, description="message not found")
    tags = await db.get_tags_for_message(user["id"], msg_id, dek)
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
    is_new = not await db.user_has_messages(uid)
    yesterday_msgs = await db.get_history(uid, yesterday_iso, dek)
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
        user_msg_id = await db.append_message(
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


def replace_newline(s: str) -> str:
    return re.sub(r"\r\n|\r|\n", "[newline]", s)


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
    history = await db.get_history(uid, date, dek)

    if not any(msg["id"] == user_msg_id for msg in history):
        actual_date = await db.get_message_date(uid, user_msg_id)
        if actual_date and actual_date != date:
            history = await db.get_history(uid, actual_date, dek)

    if not any(msg["id"] == user_msg_id for msg in history):
        logger.warning("History not found for user message %s", user_msg_id)
        return Response(
            "event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream"
        )

    existing_assistant_msg: dict | None = None
    for msg in history:
        if msg.get("reply_to") == user_msg_id and msg["role"] == "assistant":
            existing_assistant_msg = msg
            break
    if existing_assistant_msg is None:
        for idx, msg in enumerate(history):
            if msg["id"] == user_msg_id:
                if idx + 1 < len(history) and history[idx + 1]["role"] == "assistant":
                    existing_assistant_msg = history[idx + 1]
                break

    if existing_assistant_msg:

        async def saved_stream():
            yield (
                "event: message\ndata: "
                f"{replace_newline(escape(existing_assistant_msg['message']))}\n\n"
            )
            # meta = existing_assistant_msg.get("meta")
            # if meta:
            #     meta_str = orjson.dumps(meta).decode()
            #     yield f"event: meta\ndata: {escape(meta_str)}\n\n"
            data = orjson.dumps(
                {"assistant_msg_id": existing_assistant_msg["id"]}
            ).decode()
            yield f"event: done\ndata: {escape(data)}\n\n"

        return Response(saved_stream(), mimetype="text/event-stream")

    params = None
    cfg = request.args.get("config")
    if cfg:
        try:
            raw = orjson.loads(cfg)
            if isinstance(raw, dict):
                allowed = current_app.config.get("ALLOWED_LLM_CONFIG_KEYS", set())
                params = {k: raw[k] for k in raw if k in allowed}
            else:
                logger.warning("Invalid config JSON for message %s", user_msg_id)
        except Exception:
            logger.warning("Invalid config JSON for message %s", user_msg_id)

    if not params:
        params = None

    user_time_str = request.args.get("user_time")
    if not user_time_str:
        user_time_str = datetime.utcnow().isoformat() + "Z"
    tz = request.cookies.get("tz") or "UTC"
    date_str, pod = date_and_part(user_time_str, tz)
    ctx = {"date": date_str, "part_of_day": pod}

    pending_response = chat_stream_manager.get(user_msg_id)
    if not pending_response:
        pending_response = chat_stream_manager.start_stream(
            user_msg_id,
            uid,
            date,
            history,
            dek,
            params,
            ctx,
        )

    async def event_stream():
        async for chunk in pending_response.stream():
            if pending_response.error:
                yield f"event: error\ndata: {replace_newline(escape(chunk))}\n\n"
                yield "event: done\ndata: {}\n\n"
                return
            else:
                yield f"event: message\ndata: {replace_newline(escape(chunk))}\n\n"
        if pending_response.meta is not None:
            # Placeholder for emitting structured metadata (e.g., tags)
            pass
        data = orjson.dumps(
            {"assistant_msg_id": pending_response.assistant_msg_id}
        ).decode()
        yield f"event: done\ndata: {data}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")
