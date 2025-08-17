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
import json
import re
from weakref import WeakValueDictionary
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
        push_url = f"{push_url}?target=#{target}"
    resp.headers["HX-Push-Url"] = push_url
    user = await get_current_user()
    await db.update_state(user["id"], active_session=session_id)
    return resp


PENDING_TTL = 300  # seconds
CLEANUP_INTERVAL = 60  # seconds
pending_responses: WeakValueDictionary[str, "PendingResponse"] = WeakValueDictionary()
_cleanup_task: asyncio.Task | None = None


async def _cleanup_expired_pending() -> None:
    try:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            cutoff = asyncio.get_event_loop().time() - PENDING_TTL
            for msg_id, pending in list(pending_responses.items()):
                if pending.done or pending.created_at < cutoff:
                    pending_responses.pop(msg_id, None)
    except asyncio.CancelledError:
        pass


class PendingResponse:
    def __init__(
        self,
        msg_id: str,
        uid: str,
        session_id: str,
        history: list[dict],
        dek: bytes,
        params: dict | None = None,
    ):
        self.msg_id = msg_id
        self.text = ""
        self.done = False
        self.error = False
        self._cond = asyncio.Condition()
        self.dek = dek
        self.created_at = asyncio.get_event_loop().time()
        current_app.logger.debug("Starting generation for message %s", msg_id)
        asyncio.create_task(self._generate(uid, session_id, history, params))

    async def _generate(
        self,
        uid: str,
        session_id: str,
        history: list[dict],
        params: dict | None,
    ):
        full_response = ""
        first = True
        try:
            async for chunk in llm.stream_response(history, params):
                if isinstance(chunk, dict) and chunk.get("type") == "error":
                    full_response += f"<span class='error'>{chunk['data']}</span>"
                    self.error = True
                    break

                if first:
                    chunk = chunk.lstrip()
                    first = False

                full_response += chunk
                async with self._cond:
                    self.text = full_response
                    self._cond.notify_all()

        except Exception:
            current_app.logger.exception("Error during LLM streaming")
            full_response += (
                "<span class='error'>⚠️ An unexpected error occurred.</span>"
            )
            self.error = True
        finally:
            if not self.error and full_response.strip():
                try:
                    await db.append(
                        uid, session_id, "assistant", full_response, self.dek
                    )
                    current_app.logger.debug("Saved assistant message")
                except Exception:
                    current_app.logger.exception("Failed to save assistant message")
                    full_response += (
                        "<span class='error'>⚠️ Failed to save response.</span>"
                    )
                    self.error = True
            async with self._cond:
                self.text = full_response
                self.done = True
                self._cond.notify_all()
            pending_responses.pop(self.msg_id, None)

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
        msg_id = await db.append(uid, session_id, "user", user_text, dek)
        current_app.logger.debug("Saved user message %s", msg_id)
    except Exception:
        current_app.logger.exception("Failed to save user message")
        raise

    return await render_template(
        "partials/placeholder.html",
        user_text=user_text,
        msg_id=msg_id,
        session_id=session_id,
    )


def replace_newline(s: str) -> str:
    return re.sub(r"\r\n|\r|\n", "[newline]", s)


@chat_bp.route("/c/<session_id>/stream/<msg_id>")
@login_required
async def sse_reply(msg_id, session_id):
    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()
    history = await db.get_history(uid, session_id, dek)

    if not history:
        current_app.logger.warning("History not found for message %s", msg_id)
        return Response(
            "event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream"
        )

    params = None
    cfg = request.args.get("config")
    if cfg:
        try:
            raw = json.loads(cfg)
            if isinstance(raw, dict):
                allowed = current_app.config.get("ALLOWED_LLM_CONFIG_KEYS", set())
                params = {k: raw[k] for k in raw if k in allowed}
            else:
                current_app.logger.warning("Invalid config JSON for message %s", msg_id)
        except Exception:
            current_app.logger.warning("Invalid config JSON for message %s", msg_id)

    if not params:
        params = None

    pending = pending_responses.get(msg_id)
    if not pending:
        pending = PendingResponse(msg_id, uid, session_id, history, dek, params)
        pending_responses[msg_id] = pending

    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_expired_pending())

    async def event_stream():
        async for chunk in pending.stream():
            yield f"event: message\ndata: {replace_newline(escape(chunk))}\n\n"
        event = "error" if pending.error else "done"
        yield f"event: {event}\ndata: \n\n"
        pending_responses.pop(msg_id, None)

    return Response(event_stream(), mimetype="text/event-stream")
