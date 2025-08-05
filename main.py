from flask import (
    Flask,
    render_template,
    render_template_string,
    request,
    redirect,
    Response,
    stream_with_context,
    make_response,
)
import os
import uuid
import html
from llm_backend import LLMEngine
from db import HistoryDB
from dotenv import load_dotenv

load_dotenv()

llm = LLMEngine(model_path=os.environ["CHAT_MODEL_GGUF"])


def html_encode_whitespace(text):
    """Encode text for safe streaming via SSE (preserve whitespace)."""
    return html.escape(text).replace("\n", "<br>")


app = Flask(__name__)
db = HistoryDB()


@app.route("/")
def index():
    return redirect(f"/s/{db.create_session()}", code=302)


@app.route("/s/<session_id>")
def session(session_id):
    if not db.session_exists(session_id):
        return render_template("partials/error.html", message="Session not found."), 404

    history = db.get_session(session_id)

    prev_id = db.get_adjacent_session(session_id, "prev")
    next_id = db.get_adjacent_session(session_id, "next")

    html = render_template(
        "index.html",
        history=history,
        session_id=session_id,
        prev_id=prev_id,
        next_id=next_id,
    )

    response = make_response(html)
    return response


@app.route("/s/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    if not db.session_exists(session_id):
        return render_template("partials/error.html", message="Session not found."), 404

    next_url = "/s/" + (
        db.get_adjacent_session(session_id, "next")
        or db.get_adjacent_session(session_id, "prev")
        or db.create_session()
    )

    db.delete_session(session_id)

    return "", 204, {"HX-Redirect": next_url}


@app.route("/s/<session_id>/message", methods=["POST"])
def send_message(session_id):
    user_text = request.form.get("message", "")
    user_text = user_text.strip()

    if not user_text or not session_id or not db.session_exists(session_id):
        # Return partial with error message (will render in #errors div)
        return (
            render_template(
                "partials/error.html", message="Message is empty or session is invalid."
            ),
            400,
        )

    if not user_text or not session_id:
        return "", 204

    msg_id = uuid.uuid4().hex

    db.append(session_id, "user", user_text)

    html = render_template(
        "partials/placeholder.html",
        user_text=user_text,
        msg_id=msg_id,
        session_id=session_id,
    )

    return make_response(html)


@app.route("/s/<session_id>/sse-reply/<msg_id>")
def sse_reply(msg_id, session_id):
    history = db.get_session(session_id)

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
                db.append(session_id, "assistant", full_response)

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")


@app.errorhandler(Exception)
def handle_exception(e):
    return (
        render_template("partials/error.html", message="Something went wrong"),
        500,
    )
