from flask import Flask, render_template, request, Response, stream_with_context
from markupsafe import Markup
import requests
import uuid
import time

from llm_backend import stream_response

app = Flask(__name__)

messages = []
chat_sessions = {}  # uuid -> {'user': ..., 'bot': ...}

@app.route("/")
def index():
    return render_template("index.html", messages=messages)


@app.route("/messages", methods=["POST"])
def send_message():
    user_text = request.form.get("message", "").strip()
    if user_text:
        msg_id = str(uuid.uuid4())
        chat_sessions[msg_id] = {"user": user_text, "bot": None}
        messages.append({"role": "user", "text": user_text})

        placeholder_html = f'''
            <div class="user">{user_text}</div>
            <div id="bot-{msg_id}" class="bot"
                 hx-ext="sse" sse-close="done"
                 sse-connect="/sse-reply/{msg_id}">
                <div sse-swap="message" hx-swap="beforeend"></div>
            </div>
        '''
        return Markup(placeholder_html)
    return "", 204


@app.route("/sse-reply/<msg_id>")
def sse_reply(msg_id):
    entry = chat_sessions.get(msg_id)
    if not entry:
        return Response("event: error\ndata: Invalid ID\n\n", mimetype="text/event-stream")

    user_msg = entry["user"]

    def event_stream():
        yield "retry: 300\n\n"
        full_response = ""
        for chunk in stream_response(user_msg):
            full_response += chunk
            yield f"data: {chunk}\n\n"

        yield "event: done\ndata: end\n\n"  # This triggers sse-close="done"
        messages.append({"role": "bot", "text": full_response, "id": msg_id})
        del chat_sessions[msg_id]

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")
