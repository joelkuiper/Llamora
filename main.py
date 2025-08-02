from flask import (
    Flask,
    render_template,
    request,
    Response,
    stream_with_context,
    make_response,
)
import uuid
import html
from threading import Lock
import llm_backend as llm
from dotenv import load_dotenv

load_dotenv()


def html_encode_whitespace(text):
    """Encode text for safe streaming via SSE (preserve whitespace)."""
    return html.escape(text).replace("\n", "<br>")


app = Flask(__name__)


class SessionStore:
    def __init__(self):
        self.data = {}
        self.lock = Lock()

    def append(self, sid, role, text):
        with self.lock:
            self.data.setdefault(sid, []).append({"role": role, "text": text})

    def get(self, sid):
        with self.lock:
            return list(self.data.get(sid, []))  # defensive copy


session_store = SessionStore()


@app.route("/")
@app.route("/<session_id>")
def index(session_id=None):
    if not session_id:
        session_id = uuid.uuid4().hex

    messages = session_store.get(session_id)
    return render_template("index.html", messages=messages, session_id=session_id)


@app.route("/messages/<session_id>", methods=["POST"])
def send_message(session_id):
    user_text = request.form.get("message", "").strip()

    if not user_text or not session_id:
        return "", 204

    msg_id = uuid.uuid4().hex

    session_store.append(session_id, "user", user_text)

    html = render_template(
        "partials/placeholder.html",
        user_text=user_text,
        msg_id=msg_id,
        session_id=session_id,
    )

    response = make_response(html)
    response.headers["HX-Push-Url"] = f"/{session_id}"
    return response


@app.route("/sse-reply/<session_id>/<msg_id>")
def sse_reply(msg_id, session_id):
    history = session_store.get(session_id)

    if not history:
        return Response(
            "event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream"
        )

    def event_stream():
        full_response = ""
        first = True

        for chunk in llm.stream_response(history):
            if first:
                chunk = chunk.lstrip()
                first = False

            full_response += chunk
            yield f"event: message\ndata: <span>{html_encode_whitespace(chunk)}</span>\n\n"

        yield "event: done\ndata: \n\n"  # This triggers sse-close="done"

        session_store.append(session_id, "bot", full_response)

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")
