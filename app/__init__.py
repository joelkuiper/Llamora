from flask import Flask
from dotenv import load_dotenv
import os
from db import LocalDB

load_dotenv()

db = LocalDB()

def create_app():
    app = Flask(__name__)

    from .routes.auth import auth_bp
    from .routes.chat import chat_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)

    from .services.auth_helpers import load_user
    app.before_request(load_user)

    return app
