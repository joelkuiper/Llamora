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
import orjson
import re
from llm.llm_engine import LLMEngine
from app import db
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)

chat_bp = Blueprint("chat", __name__)


llm = LLMEngine()


async def render_chat(session_id, oob=False):
    user = await get_current_user()
    uid = user["id"]
    session = await db.get_session(uid, session_id)

    if not session:
        current_app.logger.warning("Session not found for user")
        abort(404, description="Session not found.")

    dek = get_dek()
    history = await db.get_history(uid, session_id, dek)
    pending_msg_id = None
    if history and history[-1]["role"] == "user":
        pending_msg_id = history[-1]["id"]

    html = await render_template(
        "partials/chat.html",
        session=session,
        history=history,
        oob=oob,
        pending_msg_id=pending_msg_id,
        user=user,
    )

    return html


@chat_bp.route("/c/<session_id>")
@login_required
async def chat_htmx(session_id):
    target = request.args.get("target")
    html = await render_chat(session_id, False)
    resp = await make_response(html, 200)
    push_url = url_for("sessions.session", session_id=session_id)
    if target:
        push_url = f"{push_url}?target={target}"
    resp.headers["HX-Push-Url"] = push_url
    user = await get_current_user()
    await db.update_state(user["id"], active_session=session_id)
    return resp


@chat_bp.route("/c/stop/<user_msg_id>", methods=["POST"])
@login_required
async def stop_generation(user_msg_id: str):
    current_app.logger.info("Stop requested for user message %s", user_msg_id)
    pending_response = pending_responses.get(user_msg_id)
    handled = False
    if pending_response:
        current_app.logger.debug("Cancelling pending response %s", user_msg_id)
        await pending_response.cancel()
        handled = True
    else:
        current_app.logger.debug(
            "No pending response for %s, aborting active stream", user_msg_id
        )
        handled = await llm.abort(user_msg_id)
    if not handled:
        current_app.logger.warning(
            "Stop request for unknown user message %s", user_msg_id
        )
        return Response("unknown message id", status=404)
    current_app.logger.debug(
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
    session_id = await db.get_message_session(user["id"], msg_id)
    if not session_id:
        abort(404, description="message not found")
    tags = await db.get_tags_for_message(user["id"], msg_id, dek)
    html = await render_template(
        "partials/meta_chips_wrapper.html", msg_id=msg_id, tags=tags, hidden=True
    )
    return html


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
        session_id: str,
        history: list[dict],
        dek: bytes,
        params: dict | None = None,
    ):
        self.user_msg_id = user_msg_id
        self.text = ""
        self.done = False
        self.error = False
        self._cond = asyncio.Condition()
        self.dek = dek
        self.meta = None
        self.cancelled = False
        self.created_at = asyncio.get_event_loop().time()
        self.assistant_msg_id: str | None = None
        current_app.logger.debug("Starting generation for user message %s", user_msg_id)
        self._task = asyncio.create_task(
            self._generate(uid, session_id, history, params)
        )

    async def _generate(
        self,
        uid: str,
        session_id: str,
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
        escape = False
        first = True
        try:
            async for chunk in llm.stream_response(self.user_msg_id, history, params):
                if isinstance(chunk, dict) and chunk.get("type") == "error":
                    full_response += f"<span class='error'>{chunk['data']}</span>"
                    self.error = True
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
                            if escape:
                                escape = False
                            elif ch == "\\":
                                escape = True
                            elif ch == '"':
                                in_str = False
                    if brace == 0 and meta_buf.strip():
                        break

        except asyncio.CancelledError:
            self.cancelled = True
            return
        except Exception:
            current_app.logger.exception("Error during LLM streaming")
            full_response += (
                "<span class='error'>⚠️ An unexpected error occurred.</span>"
            )
            self.error = True
        finally:
            if self.cancelled:
                if full_response.strip():
                    try:
                        assistant_msg_id = await db.append_message(
                            uid,
                            session_id,
                            "assistant",
                            full_response,
                            self.dek,
                            {},
                            reply_to=self.user_msg_id,
                        )
                        self.assistant_msg_id = assistant_msg_id
                        current_app.logger.debug(
                            "Saved partial assistant message %s", assistant_msg_id
                        )
                    except Exception:
                        current_app.logger.exception(
                            "Failed to save partial assistant message"
                        )
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

            if not self.error and full_response.strip():
                try:
                    assistant_msg_id = await db.append_message(
                        uid,
                        session_id,
                        "assistant",
                        full_response,
                        self.dek,
                        meta,
                        reply_to=self.user_msg_id,
                    )
                    self.assistant_msg_id = assistant_msg_id
                    current_app.logger.debug(
                        "Saved assistant message %s", assistant_msg_id
                    )
                except Exception:
                    current_app.logger.exception("Failed to save assistant message")
                    full_response += (
                        "<span class='error'>⚠️ Failed to save response.</span>"
                    )
                    self.error = True
            async with self._cond:
                self.text = full_response
                self.meta = meta
                self.done = True
                self._cond.notify_all()
            if not self.error:
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


@chat_bp.route("/c/<session_id>/message", methods=["POST"])
@login_required
async def send_message(session_id):
    form = await request.form
    user_text = form.get("message", "").strip()
    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()

    max_len = current_app.config["MAX_MESSAGE_LENGTH"]

    if (
        not user_text
        or len(user_text) > max_len
        or not await db.get_session(uid, session_id)
    ):
        abort(400, description="Message is empty, too long, or session is invalid.")

    try:
        user_msg_id = await db.append_message(uid, session_id, "user", user_text, dek)
        current_app.logger.debug("Saved user message %s", user_msg_id)
    except Exception:
        current_app.logger.exception("Failed to save user message")
        raise

    return await render_template(
        "partials/placeholder.html",
        user_text=user_text,
        user_msg_id=user_msg_id,
        session_id=session_id,
    )


def replace_newline(s: str) -> str:
    return re.sub(r"\r\n|\r|\n", "[newline]", s)


@chat_bp.route("/c/<session_id>/stream/<user_msg_id>")
@login_required
async def sse_reply(user_msg_id: str, session_id: str):
    """Stream the assistant's reply for a given user message.

    The ``user_msg_id`` corresponds to the user's prompt message. When the
    assistant finishes responding, the ``assistant_msg_id`` of the stored reply
    is sent in a final ``done`` event.
    """

    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()
    history = await db.get_history(uid, session_id, dek)

    if not history:
        current_app.logger.warning("History not found for user message %s", user_msg_id)
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
                current_app.logger.warning(
                    "Invalid config JSON for message %s", user_msg_id
                )
        except Exception:
            current_app.logger.warning(
                "Invalid config JSON for message %s", user_msg_id
            )

    if not params:
        params = None

    pending_response = pending_responses.get(user_msg_id)
    if not pending_response:
        pending_response = PendingResponse(
            user_msg_id, uid, session_id, history, dek, params
        )
        pending_responses[user_msg_id] = pending_response

    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_expired_pending())

    async def event_stream():
        async for chunk in pending_response.stream():
            yield f"event: message\ndata: {replace_newline(escape(chunk))}\n\n"
        if pending_response.meta is not None:
            # Placeholder for emitting structured metadata (e.g., tags)
            pass
        if pending_response.error:
            yield "event: error\ndata: \n\n"
        else:
            data = orjson.dumps(
                {"assistant_msg_id": pending_response.assistant_msg_id}
            ).decode()
            yield f"event: done\ndata: {data}\n\n"
            pending_responses.pop(user_msg_id, None)

    return Response(event_stream(), mimetype="text/event-stream")
