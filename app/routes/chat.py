from flask import Blueprint, render_template, redirect, request, make_response, Response, stream_with_context
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


@chat_bp.route("/s/<session_id>")
@login_required
def session(session_id):
    user = get_current_user()
    uid = user["id"]

    if not db.session_exists(uid, session_id):
        return render_template("partials/error.html", message="Session not found."), 404

    history = db.get_session(uid, session_id)
    prev_id = db.get_adjacent_session(uid, session_id, "prev")
    next_id = db.get_adjacent_session(uid, session_id, "next")

    return make_response(render_template(
        "index.html",
        user=user,
        history=history,
        session_id=session_id,
        prev_id=prev_id,
        next_id=next_id,
    ))

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

    if not db.session_exists(uid, session_id):
        return render_template("partials/error.html", message="Session not found."), 404

    next_url = "/s/" + (
        db.get_adjacent_session(uid, session_id, "next") or
        db.get_adjacent_session(uid, session_id, "prev") or
        db.create_session(uid)
    )

    db.delete_session(uid, session_id)
    return "", 204, {"HX-Redirect": next_url}


@chat_bp.route("/s/<session_id>/message", methods=["POST"])
@login_required
def send_message(session_id):
    user_text = request.form.get("message", "").strip()
    user = get_current_user()
    uid = user["id"]

    if not user_text or not db.session_exists(uid, session_id):
        return render_template("partials/error.html", message="Message is empty or session is invalid."), 400

    msg_id = uuid.uuid4().hex
    db.append(uid, session_id, "user", user_text)

    return make_response(render_template(
        "partials/placeholder.html",
        user_text=user_text,
        msg_id=msg_id,
        session_id=session_id,
    ))


@chat_bp.route("/s/<session_id>/sse-reply/<msg_id>")
@login_required
def sse_reply(msg_id, session_id):
    user = get_current_user()
    uid = user["id"]
    history = db.get_session(uid, session_id)

    if not history:
        return Response("event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream")

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

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")
