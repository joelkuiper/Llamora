from quart import Blueprint, render_template, redirect, request, current_app, make_response
from app import db
from app.services.auth_helpers import login_required, get_current_user, get_dek
from .chat import render_chat

sessions_bp = Blueprint("sessions", __name__)


@sessions_bp.route("/")
@login_required
async def index():
    user = await get_current_user()
    uid = user["id"]
    session_id = await db.get_latest_session(uid) or await db.create_session(uid)
    return redirect(f"/s/{session_id}", code=302)


@sessions_bp.route("/s/<session_id>")
@login_required
async def session(session_id):
    user = await get_current_user()
    uid = user["id"]
    session = await db.get_session(uid, session_id)

    if not session:
        current_app.logger.warning("Session not found for user")
        html = await render_template("partials/error.html", message="Session not found.")
        return await make_response(html, 404)

    dek = get_dek()
    history = await db.get_history(uid, session_id, dek)
    pending_msg_id = None
    if history and history[-1]["role"] == "user":
        pending_msg_id = history[-1]["id"]

    sessions = await db.get_all_sessions(uid)

    html = await render_template(
        "index.html",
        user=user,
        history=history,
        session=session,
        sessions=sessions,
        pending_msg_id=pending_msg_id,
    )

    return html


@sessions_bp.route("/s/create", methods=["POST"])
@login_required
async def create_session():
    user = await get_current_user()
    uid = user["id"]
    new_session_id = await db.create_session(uid)
    new_session = await db.get_session(uid, new_session_id)

    sidebar_html = await render_template(
        "partials/sidebar_session.html",
        session=new_session,
        session_id=new_session_id,
    )
    chat_html = await render_chat(new_session_id, oob=True)
    resp = await make_response(f"{chat_html}{sidebar_html}", 200)
    resp.headers["HX-Push-Url"] = f"/s/{new_session_id}"
    return resp


@sessions_bp.route("/s/<session_id>/rename", methods=["GET"])
@login_required
async def edit_session_name(session_id):
    user = await get_current_user()
    uid = user["id"]
    session = await db.get_session(uid, session_id)

    if not session:
        current_app.logger.warning("Session not found for user")
        html = await render_template("partials/error.html", message="Session not found.")
        return await make_response(html, 404)

    return await render_template(
        "partials/sidebar_session_edit.html", session=session, session_id=session_id
    )


@sessions_bp.route("/s/<session_id>/rename", methods=["PUT"])
@login_required
async def rename_session(session_id):
    user = await get_current_user()
    uid = user["id"]
    form = await request.form
    new_name = form.get("name", "").strip()
    max_len = current_app.config["MAX_SESSION_NAME_LENGTH"]
    active_session_id = request.headers.get("X-Active-Session")

    if (
        not await db.get_session(uid, session_id)
        or not new_name
        or len(new_name) > max_len
    ):
        current_app.logger.warning("Invalid rename request")
        html = await render_template(
            "partials/error.html",
            message="Error",
        )
        return await make_response(html, 400)

    await db.rename_session(uid, session_id, new_name)
    session = await db.get_session(uid, session_id)

    return await render_template(
        "partials/sidebar_session.html", session=session, session_id=active_session_id
    )


@sessions_bp.route("/s/<session_id>", methods=["DELETE"])
@login_required
async def delete_session(session_id):
    user = await get_current_user()
    uid = user["id"]

    if not await db.get_session(uid, session_id):
        current_app.logger.warning("Session not found for user")
        html = await render_template("partials/error.html", message="Session not found.")
        return await make_response(html, 404)

    active_session_id = request.headers.get("X-Active-Session")
    is_active = active_session_id == session_id

    if not is_active:
        await db.delete_session(uid, session_id)
        return ""  # Deletes it from the DOM

    else:
        next_id = await db.get_adjacent_session(uid, session_id, "next")
        prev_id = await db.get_adjacent_session(uid, session_id, "prev")

        new_session_id = next_id or prev_id
        new_session_was_created = False

        if not new_session_id:
            new_session_id = await db.create_session(uid)
            new_session_was_created = True

        await db.delete_session(uid, session_id)

        new_session = await db.get_session(uid, new_session_id)

        sidebar_html = ""
        if new_session_was_created:
            sidebar_html = await render_template(
                "partials/sidebar_session.html",
                session=new_session,
            )
            sidebar_html = f"""
          <ul hx-swap-oob="beforeend" id="sessions-list">
            {sidebar_html}
          </ul>
          """

        chat_html = await render_chat(new_session_id, oob=True)
        resp = await make_response(f"{chat_html}{sidebar_html}", 200)
        resp.headers["HX-Push-Url"] = f"/s/{new_session_id}"
        return resp
