from __future__ import annotations

import base64
import logging
import sqlite3

from quart import Blueprint, jsonify, request

from llamora.app.routes.helpers import require_user_and_dek
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.container import get_db
from llamora.app.services.lockbox import Lockbox, LockboxDecryptionError

logger = logging.getLogger(__name__)

lockbox_bp = Blueprint("lockbox_api", __name__, url_prefix="/api/lockbox")


@lockbox_bp.put("/<namespace>/<key>")
@login_required
async def put_value(namespace: str, key: str):
    _, user, dek = await require_user_and_dek()
    lockbox = _get_lockbox()
    user_id = str(user["id"])

    payload = await request.get_json(silent=True)
    encoded = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(encoded, str):
        return jsonify({"ok": False}), 400

    try:
        value = base64.b64decode(encoded, validate=True)
        await lockbox.set(user_id, dek, namespace, key, value)
    except ValueError:
        return jsonify({"ok": False}), 400
    except sqlite3.Error:
        logger.exception("Lockbox write failed")
        return jsonify({"ok": False}), 500
    except Exception:
        logger.exception("Unexpected lockbox error")
        return jsonify({"ok": False}), 500

    return jsonify({"ok": True})


@lockbox_bp.get("/<namespace>/<key>")
@login_required
async def get_value(namespace: str, key: str):
    _, user, dek = await require_user_and_dek()
    lockbox = _get_lockbox()
    user_id = str(user["id"])

    try:
        value = await lockbox.get(user_id, dek, namespace, key)
    except ValueError:
        return jsonify({"ok": False}), 400
    except LockboxDecryptionError:
        logger.warning("Lockbox decryption failed")
        return jsonify({"ok": False}), 500
    except sqlite3.Error:
        logger.exception("Lockbox read failed")
        return jsonify({"ok": False}), 500
    except Exception:
        logger.exception("Unexpected lockbox error")
        return jsonify({"ok": False}), 500

    if value is None:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "value": base64.b64encode(value).decode("ascii")})


@lockbox_bp.delete("/<namespace>/<key>")
@login_required
async def delete_value(namespace: str, key: str):
    _, user, _ = await require_user_and_dek()
    lockbox = _get_lockbox()
    user_id = str(user["id"])

    try:
        await lockbox.delete(user_id, namespace, key)
    except ValueError:
        return jsonify({"ok": False}), 400
    except sqlite3.Error:
        logger.exception("Lockbox delete failed")
        return jsonify({"ok": False}), 500
    except Exception:
        logger.exception("Unexpected lockbox error")
        return jsonify({"ok": False}), 500

    return jsonify({"ok": True})


@lockbox_bp.get("/<namespace>")
@login_required
async def list_keys(namespace: str):
    _, user, _ = await require_user_and_dek()
    lockbox = _get_lockbox()
    user_id = str(user["id"])

    try:
        keys = await lockbox.list(user_id, namespace)
    except ValueError:
        return jsonify({"ok": False}), 400
    except sqlite3.Error:
        logger.exception("Lockbox list failed")
        return jsonify({"ok": False}), 500
    except Exception:
        logger.exception("Unexpected lockbox error")
        return jsonify({"ok": False}), 500

    return jsonify({"ok": True, "keys": keys})


def _get_lockbox() -> Lockbox:
    db = get_db()
    if db.pool is None:
        raise RuntimeError("Database pool is not initialized")
    return Lockbox(db.pool)
