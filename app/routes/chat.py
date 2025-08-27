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
import asyncio
import logging
import orjson
import re
from datetime import datetime
from llm.llm_engine import LLMEngine
from llm.prompt_template import build_opening_prompt
from app import db
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)
from app.services.time import (
    local_date,
    date_and_part,
    get_timezone,
    format_date,
    part_of_day,
)

from datetime import timedelta
from zoneinfo import ZoneInfo
from ulid import ULID


chat_bp = Blueprint("chat", __name__)


llm = LLMEngine()


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
    pending_response = pending_responses.get(user_msg_id)
    handled = False
    if pending_response:
        logger.debug("Cancelling pending response %s", user_msg_id)
        await pending_response.cancel()
        handled = True
    else:
        logger.debug("No pending response for %s, aborting active stream", user_msg_id)
        handled = await llm.abort(user_msg_id)
    if not handled:
        logger.warning("Stop request for unknown user message %s", user_msg_id)
        return Response("unknown message id", status=404)
    logger.debug(
        "Stop request handled for %s (pending=%s)",
        user_msg_id,
        bool(pending_response),
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
            yield f"event: error\\ndata: {replace_newline(escape(msg))}\\n\\n"
            yield "event: done\\ndata: {}\\n\\n"
        return Response(error_stream(), mimetype="text/event-stream")
    stream_id = str(ULID())
    pending = PendingResponse(
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
                yield f"event: error\ndata: {replace_newline(chunk)}\n\n"
                yield "event: done\ndata: {}\n\n"
                return
            else:
                yield f"event: message\ndata: {replace_newline(escape(chunk))}\n\n"
        data = orjson.dumps({"assistant_msg_id": pending.assistant_msg_id}).decode()
        yield f"event: done\ndata: {data}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


PENDING_TTL = 300  # seconds
CLEANUP_INTERVAL = 60  # seconds
pending_responses: dict[str, "PendingResponse"] = {}
_cleanup_task: asyncio.Task | None = None


async def _cleanup_expired_pending() -> None:
    try:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            cutoff = asyncio.get_event_loop().time() - PENDING_TTL
            for user_msg_id, pending_response in list(pending_responses.items()):
                if pending_response.done or pending_response.created_at < cutoff:
                    pending_responses.pop(user_msg_id, None)
    except asyncio.CancelledError:
        pass


class PendingResponse:
    """Tracks an in-flight assistant reply to a user's message.

    The ``user_msg_id`` identifies the original user message prompting the
    reply. Once the assistant's response is stored in the database, the new
    ``assistant_msg_id`` is recorded for reference.
    """

    def __init__(
        self,
        user_msg_id: str,
        uid: str,
        date: str,
        history: list[dict],
        dek: bytes,
        params: dict | None = None,
        context: dict | None = None,
        prompt: str | None = None,
        reply_to: str | None = None,
        meta_extra: dict | None = None,
    ):
        self.user_msg_id = user_msg_id
        self.date = date
        self.text = ""
        self.done = False
        self.error = False
        self.error_message = ""
        self._cond = asyncio.Condition()
        self.dek = dek
        self.meta = None
        self.context = context or {}
        self.prompt = prompt
        self.reply_to = reply_to if reply_to is not None else user_msg_id
        self.meta_extra = meta_extra or {}
        self.cancelled = False
        self.created_at = asyncio.get_event_loop().time()
        self.assistant_msg_id: str | None = None
        logger.debug("Starting generation for user message %s", user_msg_id)
        self._task = asyncio.create_task(self._generate(uid, history, params))

    async def _generate(
        self,
        uid: str,
        history: list[dict],
        params: dict | None,
    ):
        full_response = ""
        sentinel = "<~meta~>"
        tail = ""
        meta_buf = ""
        meta: dict | None = None
        found = False
        brace = 0
        in_str = False
        escaping = False
        first = True
        try:
            async for chunk in llm.stream_response(
                self.user_msg_id,
                history,
                params,
                self.context,
                prompt=self.prompt,
            ):
                if isinstance(chunk, dict) and chunk.get("type") == "error":
                    self.error = True
                    self.error_message = chunk.get("data", "Unknown error")
                    full_response = (
                        f"<span class='error'>{escape(self.error_message)}</span>"
                    )
                    logger.info("Error %s", chunk)
                    break

                if first:
                    chunk = chunk.lstrip()
                    first = False

                data = tail + chunk
                tail = ""

                if not found:
                    idx = data.find(sentinel)
                    if idx != -1:
                        vis = data[:idx]
                        if vis:
                            full_response += vis
                            async with self._cond:
                                self.text = full_response
                                self._cond.notify_all()
                        data = data[idx + len(sentinel) :]
                        found = True
                        meta_buf += data
                    else:
                        keep = len(data) - len(sentinel) + 1
                        if keep > 0:
                            vis = data[:keep]
                            full_response += vis
                            async with self._cond:
                                self.text = full_response
                                self._cond.notify_all()
                            tail = data[keep:]
                        else:
                            tail = data
                        continue
                else:
                    meta_buf += data

                if found:
                    for ch in data:
                        if not in_str:
                            if ch == "{":
                                brace += 1
                            elif ch == "}":
                                brace -= 1
                                if brace == 0:
                                    break
                            elif ch == '"':
                                in_str = True
                        else:
                            if escaping:
                                escaping = False
                            elif ch == "\\":
                                escaping = True
                            elif ch == '"':
                                in_str = False
                    if brace == 0 and meta_buf.strip():
                        break

        except asyncio.CancelledError:
            self.cancelled = True
            return
        except Exception as exc:
            logger.exception("Error during LLM streaming")
            self.error = True
            self.error_message = str(exc) or "An unexpected error occurred."
            full_response += f"<span class='error'>{escape(self.error_message)}</span>"
        finally:
            if self.cancelled:
                if full_response.strip():
                    meta = {"error": True} if self.error else {}
                    meta.update(self.meta_extra)
                    try:
                        assistant_msg_id = await db.append_message(
                            uid,
                            "assistant",
                            full_response,
                            self.dek,
                            meta,
                            reply_to=self.reply_to,
                            created_date=self.date,
                        )
                        self.assistant_msg_id = assistant_msg_id
                        logger.debug(
                            "Saved partial assistant message %s", assistant_msg_id
                        )
                    except Exception:
                        logger.exception("Failed to save partial assistant message")
                        full_response += (
                            "<span class='error'>⚠️ Failed to save response.</span>"
                        )
                        self.error = True
                async with self._cond:
                    self.text = full_response
                    self.meta = None
                    self.done = True
                    self._cond.notify_all()
                pending_responses.pop(self.user_msg_id, None)
                return

            if found and meta_buf.strip():
                try:
                    meta = orjson.loads(meta_buf)
                except Exception:
                    meta = {}
            else:
                meta = {}
            if self.error:
                meta["error"] = True
            if self.meta_extra:
                meta.update(self.meta_extra)
            if full_response.strip():
                try:
                    assistant_msg_id = await db.append_message(
                        uid,
                        "assistant",
                        full_response,
                        self.dek,
                        meta,
                        reply_to=self.reply_to,
                        created_date=self.date,
                    )
                    self.assistant_msg_id = assistant_msg_id
                    logger.debug("Saved assistant message %s", assistant_msg_id)
                except Exception:
                    logger.exception("Failed to save assistant message")
                    full_response += (
                        "<span class='error'>⚠️ Failed to save response.</span>"
                    )
                    self.error = True
            async with self._cond:
                self.text = full_response
                self.meta = meta
                self.done = True
                self._cond.notify_all()
            pending_responses.pop(self.user_msg_id, None)

    async def cancel(self):
        self.cancelled = True
        await llm.abort(self.user_msg_id)
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        pending_responses.pop(self.user_msg_id, None)

    async def stream(self):
        sent = 0
        while True:
            async with self._cond:
                while len(self.text) == sent and not self.done:
                    await self._cond.wait()
                chunk = self.text[sent:]
                sent = len(self.text)
                if chunk:
                    yield chunk
                if self.done:
                    break


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
    date_str, time_of_day = date_and_part(user_time_str, tz)
    ctx = {"date": date_str, "time_of_day": time_of_day}

    pending_response = pending_responses.get(user_msg_id)
    if not pending_response:
        pending_response = PendingResponse(
            user_msg_id, uid, date, history, dek, params, ctx
        )
        pending_responses[user_msg_id] = pending_response

    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_expired_pending())

    async def event_stream():
        async for chunk in pending_response.stream():
            if pending_response.error:
                yield f"event: error\\ndata: {replace_newline(chunk)}\\n\\n"
                yield "event: done\\ndata: {}\\n\\n"
                pending_responses.pop(user_msg_id, None)
                return
            else:
                yield f"event: message\\ndata: {replace_newline(escape(chunk))}\\n\\n"
        if pending_response.meta is not None:
            # Placeholder for emitting structured metadata (e.g., tags)
            pass
        data = orjson.dumps(
            {"assistant_msg_id": pending_response.assistant_msg_id}
        ).decode()
        yield f"event: done\ndata: {data}\n\n"
        pending_responses.pop(user_msg_id, None)

    return Response(event_stream(), mimetype="text/event-stream")
