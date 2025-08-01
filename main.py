from flask import Flask, render_template, request
from markupsafe import Markup
import requests
import uuid
import time

from llm_backend import generate_response

app = Flask(__name__)

messages = []
chat_sessions = {}  # uuid -> {'user': ..., 'bot': ...}

@app.route("/")
def index():
    return render_template("index.html", messages=messages)


@app.route("/messages", methods=['POST'])
def send_message():
    user_text = request.form.get('message', '').strip()
    if user_text:
        messages.append({'role': 'user', 'text': user_text})

        # Generate a unique ID for this bot response
        msg_id = str(uuid.uuid4().hex)
        chat_sessions[msg_id] = {'user': user_text, 'bot': None}

        # Add a placeholder in the frontend for the bot's response
        placeholder_html = f'''
            <div class="user">{user_text}</div>
            <div id="{msg_id}" class="bot"
                 hx-get="/bot-reply/{msg_id}"
                 hx-trigger="load"
                 hx-swap="outerHTML">Bot is typing...</div>
        '''
        return Markup(placeholder_html)
    return '', 204

@app.route('/bot-reply/<msg_id>')
def bot_reply(msg_id):
    entry = chat_sessions.get(msg_id)
    if not entry:
        return Markup(f'<div class="bot">[Error: Unknown message ID]</div>')

    # Stub bot response based on user text
    user_msg = entry['user']
    bot_response = generate_response(user_msg)

    # Save bot response
    entry['bot'] = bot_response
    messages.append({'role': 'bot', 'text': bot_response, 'id': msg_id})

    del chat_sessions[msg_id]

    return Markup(f'<div class="bot" id="{msg_id}">{bot_response}</div>')
