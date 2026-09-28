"""Every route, three ways: signed out, as another user, and without a CSRF token.

The route table below must list every rule the app serves (checked against the
app's own url_map), so a new route can't slip in without an access check.

Alice owns some data (an entry, a trace, an image, a search, a lockbox value),
each carrying a unique marker. Bob, signed in as himself, then tries every route
with Alice's ids and dates: he must get refusals or his own (empty) views, and
never any of Alice's markers. Afterwards Alice's data must be intact.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from harness import REPO_ROOT, ApiClient, LiveServer, User, marker, today_utc
from imaging import encode, halves

DAY = today_utc() - timedelta(days=2)


@dataclass(frozen=True, slots=True)
class Route:
    method: str
    rule: str
    # "owned": carries Alice's ids (Bob must be refused); "hashed": a trace hash,
    # keyed per user, so Alice's matches nothing of Bob's (he may get an empty
    # answer); "dated": a date Alice wrote on (Bob sees only his own day);
    # "self": the caller's own data only (lockbox namespaces are per user).
    scope: str
    data: dict[str, str] = field(default_factory=dict)
    json_body: dict | None = None
    files: bool = False

    @property
    def id(self) -> str:
        return f"{self.method} {self.rule}"

    @property
    def unsafe(self) -> bool:
        return self.method in {"POST", "PUT", "PATCH", "DELETE"}


ROUTES = [
    # pages and fragments of one's own diary
    Route("GET", "/", "self"),
    Route("GET", "/calendar", "self"),
    Route("GET", "/calendar/<int:year>/<int:month>", "dated"),
    Route("GET", "/d/today", "self"),
    Route("GET", "/d/<date>", "dated"),
    Route("GET", "/d/<date>/summary", "dated"),
    Route("GET", "/e/today", "self"),
    Route("GET", "/e/<date>", "dated"),
    Route("POST", "/e/<date>/entry", "dated", data={"text": "Bob was here"}),
    Route("GET", "/e/opening/<date>", "dated"),
    # one entry
    Route("POST", "/e/<date>/response/<entry_id>", "owned"),
    Route("GET", "/e/<date>/response/stream/<entry_id>", "owned"),
    Route("POST", "/e/response/stop/<entry_id>", "owned"),
    Route("GET", "/e/actions/<entry_id>", "owned"),
    Route("GET", "/e/entry-tags/<entry_id>", "owned"),
    Route("GET", "/e/entry/<entry_id>/edit", "owned"),
    Route("GET", "/e/entry/<entry_id>/main", "owned"),
    Route("PUT", "/e/entry/<entry_id>", "owned", data={"text": "Overwritten"}),
    Route("PATCH", "/e/entry/<entry_id>", "owned", data={"text": "Overwritten"}),
    Route("DELETE", "/e/entry/<entry_id>", "owned"),
    # traces
    Route("GET", "/t", "self"),
    Route("GET", "/t/<path:tag>", "self"),
    Route("GET", "/emoji/suggest", "self"),
    Route("POST", "/t/entry/<entry_id>", "owned", data={"tag": "planted"}),
    Route("DELETE", "/t/entry/<entry_id>/<tag_hash>", "owned"),
    Route("GET", "/t/entry/<entry_id>/suggestions", "owned"),
    Route("GET", "/t/detail/<tag_hash>", "owned"),
    Route("GET", "/t/detail/<tag_hash>/entries", "hashed"),
    Route("GET", "/t/detail/<tag_hash>/summary", "owned"),
    Route("DELETE", "/t/detail/<tag_hash>/trace", "hashed"),
    Route("GET", "/fragments/tags/<date>/detail", "dated"),
    Route("GET", "/fragments/tags/<date>/heatmap", "dated"),
    Route("GET", "/fragments/tags/<date>/detail/<tag_hash>/entries", "hashed"),
    # images
    Route("POST", "/i", "self", files=True),
    Route("GET", "/i/<image_id>/<variant>", "owned"),
    Route("DELETE", "/i/<image_id>", "owned"),
    # search
    Route("GET", "/search", "self"),
    Route("GET", "/search/recent", "self"),
    # the encrypted key-value store behind client caches
    Route("GET", "/api/lockbox/<namespace>", "self"),
    Route("GET", "/api/lockbox/<namespace>/<key>", "self"),
    Route("PUT", "/api/lockbox/<namespace>/<key>", "self", json_body={"value": "x"}),
    Route("DELETE", "/api/lockbox/<namespace>/<key>", "self"),
    # the account
    Route("GET", "/profile", "self"),
    Route("GET", "/profile/tab/<tab>", "self"),
    Route("GET", "/profile/data", "self"),
    Route("POST", "/profile/password", "self"),
    Route("POST", "/profile/recovery", "self"),
    Route("DELETE", "/profile", "self"),
    Route("POST", "/logout", "self"),
]

# Reachable signed out by design (signing in, registering, assets).
PUBLIC = {
    ("GET", "/login"),
    ("POST", "/login"),
    ("GET", "/register"),
    ("POST", "/register"),
    ("GET", "/reset"),
    ("POST", "/reset"),
    ("POST", "/password_strength"),
    ("GET", "/static/<path:filename>"),
}

UNSAFE = [r for r in ROUTES if r.unsafe]
# Routes that end the caller's own session or account: only tried without a
# CSRF token (which must stop them), never for real.
ENDS_SESSION = {"/logout", "/profile", "/profile/password", "/profile/recovery"}


@dataclass(slots=True)
class Alice:
    client: ApiClient
    entry_id: str
    tag: str
    tag_hash: str
    image_id: str
    namespace: str
    key: str
    markers: tuple[str, ...]


def path_for(route: Route, alice: Alice) -> str:
    values = {
        "<date>": DAY.isoformat(),
        "<int:year>": str(DAY.year),
        "<int:month>": str(DAY.month),
        "<entry_id>": alice.entry_id,
        "<tag_hash>": alice.tag_hash,
        "<path:tag>": alice.tag,
        "<image_id>": alice.image_id,
        "<variant>": "thumb",
        "<namespace>": alice.namespace,
        "<key>": alice.key,
        "<tab>": "account",
    }
    path = route.rule
    for placeholder, value in values.items():
        path = path.replace(placeholder, value)
    assert "<" not in path, f"no value for a placeholder in {route.rule}"
    return path


def send(
    client: httpx.Client, route: Route, alice: Alice, headers: dict[str, str]
) -> tuple[int, str, str]:
    """(status, location, first bytes of the body); streams are cut short."""
    kwargs: dict = {"headers": headers}
    if route.data:
        kwargs["data"] = route.data
    if route.json_body is not None:
        kwargs["json"] = route.json_body
    if route.files:
        kwargs["files"] = {"image": ("p.jpg", PHOTO, "image/jpeg")}
    params = {"q": "quiet evenings"} if route.rule == "/search" else None
    body = b""
    with client.stream(
        route.method,
        path_for(route, alice),
        params=params,
        timeout=httpx.Timeout(20.0),
        **kwargs,
    ) as resp:
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > 256_000:
                break
        return (
            resp.status_code,
            resp.headers.get("location", ""),
            body.decode("utf-8", "replace"),
        )


PHOTO = encode(halves((64, 48)), "JPEG")


@pytest.fixture(scope="module")
def alice(live_server: LiveServer, make_user: Callable[..., User]) -> Iterator[Alice]:
    client = ApiClient.logged_in(live_server.url, make_user("alice"))
    text_marker, tag, search_marker, box_marker = (
        marker("secretword"),
        marker("secrettrace"),
        marker("secretquery"),
        marker("secretbox"),
    )
    image_id = client.upload_image_id(PHOTO)
    entry_id = client.create_entry(
        DAY, f"Alice's private note {text_marker}", image_ids=[image_id]
    )
    client.add_tag(entry_id, tag)
    tags_html = client.http.get(f"/e/entry-tags/{entry_id}", headers=client.headers)
    found = re.search(r'data-tag-hash="([0-9a-fA-F]+)"', tags_html.text)
    assert found, "tag hash not found"
    client.http.get(
        "/search", params={"q": search_marker}, headers=client.headers
    ).raise_for_status()
    namespace, key = "security-test", "k1"
    client.http.put(
        f"/api/lockbox/{namespace}/{key}",
        json={"value": box_marker},
        headers=client.headers,
    ).raise_for_status()
    yield Alice(
        client=client,
        entry_id=entry_id,
        tag=tag,
        tag_hash=found.group(1),
        image_id=image_id,
        namespace=namespace,
        key=key,
        markers=(text_marker, tag, search_marker, box_marker),
    )
    client.close()


@pytest.fixture(scope="module")
def bob(live_server: LiveServer, make_user: Callable[..., User]) -> Iterator[ApiClient]:
    client = ApiClient.logged_in(live_server.url, make_user("bob"))
    yield client
    client.close()


def leaks(body: str, alice: Alice, route: Route | None = None) -> list[str]:
    asked = path_for(route, alice) if route else ""
    return [m for m in alice.markers if m in body and m not in asked]


# -- the table is complete ----------------------------------------------------


def app_routes(tmp_path: Path) -> set[tuple[str, str]]:
    """Every (method, rule) the app serves, from its own url_map."""
    script = (
        "import json\n"
        "from llamora.app import create_app\n"
        "app = create_app()\n"
        "print(json.dumps(sorted({(m, r.rule) for r in app.url_map.iter_rules()"
        " for m in r.methods - {'HEAD', 'OPTIONS'}})))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMORA_")}
    env["LLAMORA_LLM__UPSTREAM__HOST"] = "http://127.0.0.1:9"
    env["LLAMORA_DATABASE__PATH"] = str(tmp_path / "routes.sqlite3")
    env["LLAMORA_IMAGES__PATH"] = str(tmp_path / "images")
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return {tuple(pair) for pair in json.loads(out.stdout.strip().splitlines()[-1])}


def test_every_route_is_in_the_table(tmp_path: Path) -> None:
    listed = {(r.method, r.rule) for r in ROUTES} | PUBLIC
    served = app_routes(tmp_path)
    assert served - listed == set(), "new routes: add them to ROUTES (or PUBLIC)"
    assert listed - served == set(), "routes listed here that the app no longer has"


# -- signed out ---------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.id)
def test_signed_out_gets_nothing(
    live_server: LiveServer, alice: Alice, route: Route
) -> None:
    with httpx.Client(base_url=live_server.url) as anonymous:
        status, location, body = send(anonymous, route, alice, {"HX-Request": "true"})
        assert status >= 300, (status, body[:200])
        if 300 <= status < 400:
            assert location.startswith("/login"), location
        assert leaks(body, alice) == []

        status, location, body = send(anonymous, route, alice, {})
        assert status >= 300, (status, body[:200])
        if 300 <= status < 400:
            assert location.startswith("/login"), location


# -- another user ---------------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    [r for r in ROUTES if r.rule not in ENDS_SESSION],
    ids=lambda r: r.id,
)
def test_another_user_never_sees_or_changes_it(
    bob: ApiClient, alice: Alice, route: Route
) -> None:
    status, _, body = send(bob.http, route, alice, bob.headers)

    assert leaks(body, alice, route) == [], (status, body[:300])
    if route.scope == "owned":
        # A stream has already answered 200 when it reports the refusal.
        refused = status >= 400 or body.startswith("event: error")
        assert refused, (status, body[:300])
    if route.unsafe:
        assert_alice_intact(alice)


def assert_alice_intact(alice: Alice) -> None:
    """Alice is still signed in and her entry, trace, image, search and
    lockbox value are all as she left them."""
    client = alice.client
    assert client.http.get("/d/today").status_code == 200
    day = client.http.get(f"/d/{DAY.isoformat()}")
    text_marker, tag, search_marker, box_marker = alice.markers
    assert text_marker in day.text
    assert "Overwritten" not in day.text and "Bob was here" not in day.text
    tags = client.http.get(f"/e/entry-tags/{alice.entry_id}", headers=client.headers)
    assert tag in tags.text and "planted" not in tags.text
    assert client.get_image(alice.image_id, "thumb").status_code == 200
    box = client.http.get(
        f"/api/lockbox/{alice.namespace}/{alice.key}", headers=client.headers
    )
    assert box.json() == {"ok": True, "value": box_marker}
    recent = client.http.get("/search/recent", headers=client.headers)
    assert search_marker in recent.text


# -- CSRF -----------------------------------------------------------------------


@pytest.mark.parametrize("route", UNSAFE, ids=lambda r: r.id)
def test_unsafe_requests_need_the_csrf_token(alice: Alice, route: Route) -> None:
    status, _, body = send(alice.client.http, route, alice, {"HX-Request": "true"})

    assert status == 400, (status, body[:200])
    assert_alice_intact(alice)
