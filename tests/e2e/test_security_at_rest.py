"""Nothing a user writes is stored readably: after a session that exercises
every kind of content, the server's files on disk hold none of it in plain text.

Every piece of content carries a unique marker; the whole data directory (the
SQLite database with its WAL, the image store, any index files) is then read
raw and searched for each marker, as UTF-8 and UTF-16 (SQLite's other text
encoding). Only the server log and the config are left out.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from fake_llm import FakeLLM
from harness import ApiClient, LiveServer, User, marker, today_utc
from imaging import encode, halves

DAY = today_utc() - timedelta(days=1)


def data_files(server: LiveServer) -> list[Path]:
    root = server.log_path.parent
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path != server.log_path and "config" not in path.parts
    ]


def found_in_plain_text(
    server: LiveServer, markers: dict[str, str]
) -> dict[str, list[str]]:
    blobs = {path: path.read_bytes() for path in data_files(server)}
    found: dict[str, list[str]] = {}
    for what, word in markers.items():
        needles = (word.encode("utf-8"), word.encode("utf-16-le"))
        hits = [
            str(path.relative_to(server.log_path.parent))
            for path, blob in blobs.items()
            if any(needle in blob for needle in needles)
        ]
        if hits:
            found[what] = hits
    return found


def wait_until(check: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError("timed out")


def test_nothing_is_stored_in_plain_text(
    live_server: LiveServer, make_user: Callable[..., User], fake_llm: FakeLLM
) -> None:
    markers = {
        "entry": marker("plainentry"),
        "edited entry": marker("plainedit"),
        "reply": marker("plainreply"),
        "trace": marker("plaintrace"),
        "suggested trace": marker("plainsuggest"),
        "search": marker("plainsearch"),
        "lockbox": marker("plainbox"),
        "image name": marker("plainphoto"),
    }
    user = make_user("atrest")
    api = ApiClient.logged_in(live_server.url, user)
    headers = api.headers
    fake_llm.reply = f"A thoughtful reply {markers['reply']}."
    fake_llm.tags = [markers["suggested trace"]]

    image_id = api.upload_image_id(
        encode(halves((64, 48)), "JPEG"), filename=f"{markers['image name']}.jpg"
    )
    entry_id = api.create_entry(
        DAY, f"First draft {markers['entry']}", image_ids=[image_id]
    )
    api.update_entry(entry_id, f"Second thoughts {markers['edited entry']}")
    api.add_tag(entry_id, markers["trace"])
    suggestions = api.http.get(f"/t/entry/{entry_id}/suggestions", headers=headers)
    assert markers["suggested trace"] in suggestions.text

    assert api.start_reply(entry_id, DAY) == 200
    wait_until(lambda: markers["reply"] in api.http.get(f"/d/{DAY.isoformat()}").text)
    api.http.get("/search", params={"q": markers["search"]}, headers=headers)
    api.http.put(
        "/api/lockbox/at-rest/value",
        json={"value": markers["lockbox"]},
        headers=headers,
    ).raise_for_status()
    # Readable through the app, so the markers really were stored.
    recent = api.http.get("/search/recent", headers=headers).text
    assert markers["search"] in recent
    time.sleep(1.0)  # let background indexing and caching settle
    api.close()

    assert found_in_plain_text(live_server, markers) == {}
    # The scan does see plain text: usernames are stored in the clear by design.
    assert found_in_plain_text(live_server, {"username": user.username})
