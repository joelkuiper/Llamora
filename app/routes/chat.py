from flask import Blueprint, render_template, redirect, request, Response, current_app
from html import escape
import threading
import os
import re
from llm_backend import LLMEngine
from util import str_to_bool
from app import db
from app.services.auth_helpers import login_required, get_current_user

chat_bp = Blueprint("chat", __name__)


llm = LLMEngine(
    model_path=os.environ["CHAT_MODEL_GGUF"],
    verbose=str_to_bool(os.getenv("FLASK_DEBUG", "False")),
)


pending_responses: dict[str, "PendingResponse"] = {}


class PendingResponse:
    def __init__(self, msg_id: str, uid: str, session_id: str, history: list[dict]):
        self.msg_id = msg_id
        self.text = ""
        self.done = False
        self.error = False
        self._cond = threading.Condition()
        thread = threading.Thread(
            target=self._generate, args=(uid, session_id, history), daemon=True
        )
        thread.start()

    def _generate(self, uid: str, session_id: str, history: list[dict]):
        full_response = ""
        first = True
        try:
            for chunk in llm.stream_response(history):
                if isinstance(chunk, dict) and chunk.get("type") == "error":
                    full_response += f"<span class='error'>{chunk['data']}</span>"
                    self.error = True
                    break

                if first:
                    chunk = chunk.lstrip()
                    first = False

                full_response += chunk
                with self._cond:
                    self.text = full_response
                    self._cond.notify_all()
        except Exception as e:
            full_response += f"<span class='error'>⚠️ {str(e)}</span>"
            self.error = True
        finally:
            if not self.error and full_response.strip():
                db.append(uid, session_id, "assistant", full_response)
            with self._cond:
                self.text = full_response
                self.done = True
                self._cond.notify_all()
            pending_responses.pop(self.msg_id, None)

    def stream(self):
        sent = 0
        while True:
            with self._cond:
                while len(self.text) == sent and not self.done:
                    self._cond.wait()
                chunk = self.text[sent:]
                sent = len(self.text)
                if chunk:
                    yield chunk
                if self.done:
                    break


@chat_bp.route("/")
@login_required
def index():
    user = get_current_user()
    uid = user["id"]
    session_id = db.get_latest_session(uid) or db.create_session(uid)
    return redirect(f"/s/{session_id}", code=302)


@chat_bp.route("/s/<session_id>")
@login_required
def session(session_id):
    user = get_current_user()
    uid = user["id"]
    session = db.get_session(uid, session_id)

    if not session:
        return render_template("partials/error.html", message="Session not found."), 404

    history = db.get_history(uid, session_id)
    pending_msg_id = None
    if history and history[-1]["role"] == "user":
        pending_msg_id = history[-1]["id"]

    sessions = db.get_all_sessions(uid)

    html = render_template(
        "index.html",
        user=user,
        history=history,
        session=session,
        sessions=sessions,
        pending_msg_id=pending_msg_id,
    )

    return html


def render_chat(session_id, oob=False):
    user = get_current_user()
    uid = user["id"]
    session = db.get_session(uid, session_id)

    if not session:
        return render_template("partials/error.html", message="Session not found."), 404

    history = db.get_history(uid, session_id)
    pending_msg_id = None
    if history and history[-1]["role"] == "user":
        pending_msg_id = history[-1]["id"]

    html = render_template(
        "partials/chat.html",
        session=session,
        history=history,
        oob=oob,
        pending_msg_id=pending_msg_id,
    )

    return html


@chat_bp.route("/s/<session_id>/chat")
@login_required
def chat_htmx(session_id):
    html = render_chat(session_id, False)
    return html, 200, {"HX-Push-Url": f"/s/{session_id}"}


@chat_bp.route("/s/create", methods=["POST"])
@login_required
def create_session():
    user = get_current_user()
    uid = user["id"]
    new_session_id = db.create_session(uid)
    new_session = db.get_session(uid, new_session_id)

    sidebar_html = render_template(
        "partials/sidebar_session.html",
        session=new_session,
        session_id=new_session_id,
    )
    chat_html = render_chat(new_session_id, oob=True)

    return (
        f"{chat_html}{sidebar_html}",
        200,
        {"HX-Push-Url": f"/s/{new_session_id}"},
    )


@chat_bp.route("/s/<session_id>/rename", methods=["GET"])
@login_required
def edit_session_name(session_id):
    user = get_current_user()
    uid = user["id"]
    session = db.get_session(uid, session_id)

    if not session:
        return render_template("partials/error.html", message="Session not found."), 404

    return render_template(
        "partials/sidebar_session_edit.html", session=session, session_id=session_id
    )


@chat_bp.route("/s/<session_id>/rename", methods=["PUT"])
@login_required
def rename_session(session_id):
    user = get_current_user()
    uid = user["id"]
    new_name = request.form.get("name", "").strip()
    max_len = current_app.config["MAX_SESSION_NAME_LENGTH"]
    active_session_id = request.headers.get("X-Active-Session")

    if not db.get_session(uid, session_id) or not new_name or len(new_name) > max_len:
        return (
            render_template(
                "partials/error.html",
                message="Error",
            ),
            400,
        )

    db.rename_session(uid, session_id, new_name)
    session = db.get_session(uid, session_id)

    return render_template(
        "partials/sidebar_session.html", session=session, session_id=active_session_id
    )


@chat_bp.route("/s/<session_id>", methods=["DELETE"])
@login_required
def delete_session(session_id):
    user = get_current_user()
    uid = user["id"]

    if not db.get_session(uid, session_id):
        return render_template("partials/error.html", message="Session not found."), 404

    active_session_id = request.headers.get("X-Active-Session")
    is_active = active_session_id == session_id

    if not is_active:

        db.delete_session(uid, session_id)
        return ""  # Deletes it from the DOM

    else:
        next_id = db.get_adjacent_session(uid, session_id, "next")
        prev_id = db.get_adjacent_session(uid, session_id, "prev")

        new_session_id = next_id or prev_id
        new_session_was_created = False

        if not new_session_id:
            new_session_id = db.create_session(uid)
            new_session_was_created = True

        db.delete_session(uid, session_id)

        new_session = db.get_session(uid, new_session_id)

        sidebar_html = ""
        if new_session_was_created:
            sidebar_html = render_template(
                "partials/sidebar_session.html",
                session=new_session,
            )
            sidebar_html = f"""
          <ul hx-swap-oob="beforeend" id="sessions-list">
            {sidebar_html}
          </ul>
          """

        chat_html = render_chat(new_session_id, oob=True)

        return (
            f"{chat_html}{sidebar_html}",
            200,
            {"HX-Push-Url": f"/s/{new_session_id}"},
        )


@chat_bp.route("/s/<session_id>/message", methods=["POST"])
@login_required
def send_message(session_id):
    user_text = request.form.get("message", "").strip()
    user = get_current_user()
    uid = user["id"]

    max_len = current_app.config["MAX_MESSAGE_LENGTH"]

    if not user_text or len(user_text) > max_len or not db.get_session(uid, session_id):
        return (
            render_template(
                "partials/error.html",
                message="Message is empty, too long, or session is invalid.",
            ),
            400,
        )

    msg_id = db.append(uid, session_id, "user", user_text)

    return render_template(
        "partials/placeholder.html",
        user_text=user_text,
        msg_id=msg_id,
        session_id=session_id,
    )


def replace_newline(s: str) -> str:
    return re.sub(r"\r\n|\r|\n", "[newline]", s)


@chat_bp.route("/s/<session_id>/sse-reply/<msg_id>")
@login_required
def sse_reply(msg_id, session_id):
    user = get_current_user()
    uid = user["id"]
    history = db.get_history(uid, session_id)

    if not history:
        return Response(
            "event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream"
        )

    pending = pending_responses.get(msg_id)
    if not pending:
        pending = PendingResponse(msg_id, uid, session_id, history)
        pending_responses[msg_id] = pending

    def event_stream():
        for chunk in pending.stream():
            yield f"event: message\ndata: {replace_newline(escape(chunk))}\n\n"
        yield "event: done\ndata: \n\n"
        pending_responses.pop(msg_id, None)

    return Response(event_stream(), mimetype="text/event-stream")
