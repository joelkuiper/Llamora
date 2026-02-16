from __future__ import annotations

import logging
import sqlite3
import orjson

from quart import Blueprint, jsonify, request

from llamora.app.routes.helpers import require_encryption_context
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.container import get_db
from llamora.app.services.lockbox import Lockbox, LockboxDecryptionError

logger = logging.getLogger(__name__)

lockbox_bp = Blueprint("lockbox_api", __name__, url_prefix="/api/lockbox")


@lockbox_bp.put("/<namespace>/<key>")
@login_required
async def put_value(namespace: str, key: str):
    _, _, ctx = await require_encryption_context()
    lockbox = _get_lockbox()

    payload = await request.get_json(silent=True)
    if not isinstance(payload, dict) or "value" not in payload:
        return jsonify({"ok": False}), 400
    encoded = payload.get("value")

    try:
        value = orjson.dumps(encoded)
        await lockbox.set(ctx, namespace, key, value)
    except (TypeError, orjson.JSONEncodeError):
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
    _, _, ctx = await require_encryption_context()
    lockbox = _get_lockbox()

    try:
        value = await lockbox.get(ctx, namespace, key)
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
    try:
        decoded = orjson.loads(value)
    except orjson.JSONDecodeError:
        logger.exception("Lockbox decode failed")
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True, "value": decoded})


@lockbox_bp.delete("/<namespace>/<key>")
@login_required
async def delete_value(namespace: str, key: str):
    _, _, ctx = await require_encryption_context()
    lockbox = _get_lockbox()

    try:
        await lockbox.delete(ctx.user_id, namespace, key)
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
    _, _, ctx = await require_encryption_context()
    lockbox = _get_lockbox()

    try:
        keys = await lockbox.list(ctx.user_id, namespace)
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
