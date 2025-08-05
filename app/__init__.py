from flask import Flask
from dotenv import load_dotenv
import os
from llm_backend import LLMEngine
from db import LocalDB

load_dotenv()

llm = LLMEngine(model_path=os.environ["CHAT_MODEL_GGUF"])
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
