from flask import Blueprint, render_template, redirect, request, Response, current_app
import uuid
import html
import os
from llm_backend import LLMEngine

from app import db
from app.services.auth_helpers import login_required, get_current_user

chat_bp = Blueprint("chat", __name__)


llm = LLMEngine(model_path=os.environ["CHAT_MODEL_GGUF"])


def html_encode_whitespace(text):
    return html.escape(text).replace("\n", "<br>")


@chat_bp.route("/")
@login_required
def index():
    user = get_current_user()
    uid = user["id"]
    session_id = db.get_latest_session(uid) or db.create_session(uid)
    return redirect(f"/s/{session_id}", code=302)


def render_session(session_id, template="index.html", push_url=False):
    user = get_current_user()
    uid = user["id"]

    if not db.get_session(uid, session_id):
        return render_template("partials/error.html", message="Session not found."), 404

    history = db.get_history(uid, session_id)
    sessions = db.get_all_sessions(uid)
    prev_id = db.get_adjacent_session(uid, session_id, "prev")
    next_id = db.get_adjacent_session(uid, session_id, "next")

    html = render_template(
        template,
        user=user,
        history=history,
        session_id=session_id,
        sessions=sessions,
        prev_id=prev_id,
        next_id=next_id,
    )

    if push_url:
        return html, 200, {"HX-Push-Url": f"/s/{session_id}"}
    return html


@chat_bp.route("/s/<session_id>")
@login_required
def session(session_id):
    return render_session(session_id)


@chat_bp.route("/s/<session_id>/chat")
@login_required
def chat_ui(session_id):
    return render_session(session_id, template="partials/chat.html", push_url=True)


@chat_bp.route("/s/create", methods=["POST"])
@login_required
def create_session():
    user = get_current_user()
    uid = user["id"]
    session_id = db.create_session(uid)
    return "", 204, {"HX-Redirect": session_id}


@chat_bp.route("/s/<session_id>", methods=["DELETE"])
@login_required
def delete_session(session_id):
    user = get_current_user()
    uid = user["id"]

    if not db.get_session(uid, session_id):
        return render_template("partials/error.html", message="Session not found."), 404

    next_id = db.get_adjacent_session(uid, session_id, "next")
    prev_id = db.get_adjacent_session(uid, session_id, "prev")

    new_session_id = next_id or prev_id
    new_session_was_created = False

    if not new_session_id:
        new_session_id = db.create_session(uid)
        new_session_was_created = True

    db.delete_session(uid, session_id)

    if request.args.get("htmx") == "true":
        history = db.get_history(uid, new_session_id)
        new_session = db.get_session(uid, new_session_id)

        sidebar_html = ""
        if new_session_was_created:
            sidebar_html = render_template(
                "partials/sidebar_session.html",
                session=new_session,
                session_id=new_session_id,
            )
            sidebar_html = f"""
            <ul hx-swap-oob="beforeend" id="sidebar-inner">
              {sidebar_html}
            </ul>
            """

        chat_html = render_template(
            "partials/chat.html",
            user=user,
            history=history,
            session_id=new_session_id,
            prev_id=db.get_adjacent_session(uid, new_session_id, "prev"),
            next_id=db.get_adjacent_session(uid, new_session_id, "next"),
        )

        chat_html = f"""
        <main hx-swap-oob="true" id="chatbox-wrapper">
          {chat_html}
        </main>
        """

        return (
            f"{chat_html}{sidebar_html}",
            200,
            {"HX-Push-Url": f"/s/{new_session_id}"},
        )

    return "", 204, {"HX-Redirect": f"/s/{new_session_id}"}


@chat_bp.route("/s/<session_id>/message", methods=["POST"])
@login_required
def send_message(session_id):
    user_text = request.form.get("message", "").strip()
    user = get_current_user()
    uid = user["id"]

    max_len = current_app.config["MAX_MESSAGE_LENGTH"]

    if not (
        user_text or len(user_text) > max_len or not db.get_session(uid, session_id)
    ):
        return (
            render_template(
                "partials/error.html",
                message="Message is empty, too long, or session is invalid.",
            ),
            400,
        )

    msg_id = uuid.uuid4().hex
    db.append(uid, session_id, "user", user_text)

    return render_template(
        "partials/placeholder.html",
        user_text=user_text,
        msg_id=msg_id,
        session_id=session_id,
    )


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

    def event_stream():
        full_response = ""
        first = True
        error_occurred = False

        try:
            for chunk in llm.stream_response(history):
                if isinstance(chunk, dict) and chunk.get("type") == "error":
                    yield f"event: message\ndata: <span class='error'>{chunk['data']}</span>\n\n"
                    error_occurred = True
                    break

                if first:
                    chunk = chunk.lstrip()
                    first = False

                full_response += chunk
                yield f"event: message\ndata: {html_encode_whitespace(chunk)}\n\n"
        except Exception as e:
            yield f"event: message\ndata: <span class='error'>⚠️ {str(e)}</span>\n\n"
            error_occurred = True
        finally:
            yield "event: done\ndata: \n\n"
            if not error_occurred and full_response.strip():
                db.append(uid, session_id, "assistant", full_response)

    return Response(event_stream(), mimetype="text/event-stream")
