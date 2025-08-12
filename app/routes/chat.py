from quart import (
    Blueprint,
    render_template,
    request,
    Response,
    current_app,
    make_response,
    abort,
)
from html import escape
import asyncio
import os
import re
from llm_engine import LLMEngine
from app import db
from app.services.auth_helpers import login_required, get_current_user, get_dek

chat_bp = Blueprint("chat", __name__)


llm = LLMEngine(os.getenv("LLAMAFILE"))


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
    html = await render_chat(session_id, False)
    resp = await make_response(html, 200)
    resp.headers["HX-Push-Url"] = f"/s/{session_id}"
    return resp


pending_responses: dict[str, "PendingResponse"] = {}


class PendingResponse:
    def __init__(
        self, msg_id: str, uid: str, session_id: str, history: list[dict], dek: bytes
    ):
        self.msg_id = msg_id
        self.text = ""
        self.done = False
        self.error = False
        self._cond = asyncio.Condition()
        self.dek = dek
        current_app.logger.debug("Starting generation for message %s", msg_id)
        asyncio.create_task(self._generate(uid, session_id, history))

    async def _generate(self, uid: str, session_id: str, history: list[dict]):
        full_response = ""
        first = True
        try:
            async for chunk in llm.stream_response(history):
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

    pending = pending_responses.get(msg_id)
    if not pending:
        pending = PendingResponse(msg_id, uid, session_id, history, dek)
        pending_responses[msg_id] = pending

    async def event_stream():
        async for chunk in pending.stream():
            yield f"event: message\ndata: {replace_newline(escape(chunk))}\n\n"
        yield "event: done\ndata: \n\n"
        pending_responses.pop(msg_id, None)

    return Response(event_stream(), mimetype="text/event-stream")
