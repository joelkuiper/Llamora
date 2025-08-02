from flask import Flask, render_template, request, Response, stream_with_context, make_response
import requests
import uuid
import time
import html

from dotenv import load_dotenv
load_dotenv()

from llm_backend import stream_response


def html_encode_whitespace(text):
    """Encode text for safe streaming via SSE (preserve whitespace)."""
    return html.escape(text).replace("\n", "<br>")

app = Flask(__name__)

session_history = {}  # session_id -> [{"role": "user"/"bot", "text": ...}, ...]
chat_sessions = {}  # uuid -> {'session_id': ...}

@app.route("/")
@app.route("/<session_id>")
def index(session_id=None):
    if not session_id:
        session_id = uuid.uuid4().hex

    messages = session_history.get(session_id, [])
    return render_template("index.html", messages=messages, session_id=session_id)

@app.route("/messages/<session_id>", methods=["POST"])
def send_message(session_id):
    user_text = request.form.get("message", "").strip()

    if not user_text or not session_id:
        return "", 204

    msg_id = uuid.uuid4().hex
    chat_sessions[msg_id] = {"session_id": session_id}

    session_history.setdefault(session_id, []).append({
        "role": "user",
        "text": user_text})

    html = render_template("partials/placeholder.html",
                            user_text=user_text,
                            msg_id=msg_id)

    response = make_response(html)
    response.headers["HX-Push-Url"] = f"/{session_id}"
    return response


@app.route("/sse-reply/<msg_id>")
def sse_reply(msg_id):
    entry = chat_sessions.get(msg_id)
    session_id = entry["session_id"]

    if not entry:
        return Response("event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream")

    history = session_history[session_id]
    def event_stream():
        full_response = ""
        first = True

        for chunk in stream_response(history):
            if first:
                chunk = chunk.lstrip()
                first = False

            full_response += chunk
            yield f"data: <span>{html_encode_whitespace(chunk)}</span>\n\n"

        yield "event: done\ndata: \n\n"  # This triggers sse-close="done"

        session_history[session_id].append({"role": "bot", "text": full_response})
        del chat_sessions[msg_id]

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")
