from quart import Quart
from dotenv import load_dotenv
from quart_wtf import CSRFProtect
import os
from db import LocalDB

load_dotenv()

db = LocalDB()


def create_app():
    app = Quart(__name__)
    app.secret_key = os.getenv("CHAT_SECRET_KEY")
    app.config.from_object("config")

    csrf = CSRFProtect(app)

    from .routes.auth import auth_bp
    from .routes.chat import chat_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)

    from .services.auth_helpers import load_user

    app.before_request(load_user)

    return app
