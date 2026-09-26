"""Test harness: an isolated Llamora server process plus HTTP/page helpers."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from urllib.parse import unquote
from zoneinfo import ZoneInfo
from pathlib import Path

import httpx
from playwright.sync_api import Locator, Page, expect

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT = 90.0

_CSRF_INPUT_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
_RECOVERY_RE = re.compile(r'<pre id="recovery">([^<]+)</pre>')


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_config(
    config_dir: Path, *, db_path: Path, llm_url: str, extra: str = ""
) -> None:
    """Build an isolated config dir: repo defaults + test-only overrides.

    The developer's ``config/settings.local.toml`` and ``.secrets.toml`` are
    deliberately not copied, so local LLM hosts and tweaks never leak in.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "config" / "settings.toml", config_dir / "settings.toml")
    (config_dir / "settings.local.toml").write_text(
        f"""\
[default]
LOG_LEVEL = "INFO"

[default.DATABASE]
path = "{db_path}"

[default.LLM.upstream]
host = "{llm_url}"

[default.AUTH]
max_login_attempts = 1000
"""
        + extra,
        encoding="utf-8",
    )


@dataclass(slots=True)
class LiveServer:
    url: str
    process: subprocess.Popen[bytes]
    log_path: Path

    def log_tail(self, lines: int = 60) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def start_server(
    workdir: Path,
    *,
    llm_url: str,
    now: datetime | None = None,
    extra_config: str = "",
    extra_env: dict[str, str] | None = None,
) -> LiveServer:
    """Start an isolated server.

    ``now`` moves its clock (it keeps ticking); ``extra_config`` is appended to
    its settings.local.toml; ``extra_env`` adds (``LLAMORA_*``) variables.
    """
    config_dir = workdir / "config"
    write_config(
        config_dir,
        db_path=workdir / "state.sqlite3",
        llm_url=llm_url,
        extra=extra_config,
    )

    env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMORA_")}
    env["LLAMORA_CONFIG_DIR"] = str(config_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra_env or {})
    if now is not None:
        assert now.tzinfo is not None, "server clock needs an aware datetime"
        env["LLAMORA_TEST_NOW"] = now.isoformat()

    port = free_port()
    log_path = workdir / "server.log"
    log_file = log_path.open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("server_bootstrap.py")),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "prod",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    server = LiveServer(
        url=f"http://127.0.0.1:{port}", process=process, log_path=log_path
    )

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Llamora exited during startup (code {process.returncode}):\n"
                f"{server.log_tail()}"
            )
        try:
            if httpx.get(f"{server.url}/login", timeout=1.0).status_code == 200:
                return server
        except httpx.HTTPError:
            pass
        time.sleep(0.2)

    server.stop()
    raise RuntimeError(
        f"Llamora did not start within {STARTUP_TIMEOUT}s:\n{server.log_tail()}"
    )


def build_assets() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_assets.py", "build", "--mode", "prod"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )


# --- users ------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class User:
    username: str
    password: str
    recovery_code: str = ""  # shown once at registration


def new_credentials(prefix: str = "e2e") -> User:
    # Long random passphrase: comfortably passes the zxcvbn strength check.
    return User(
        username=f"{prefix}_{secrets.token_hex(4)}",
        password=f"quiet-harbor-{secrets.token_urlsafe(12)}",
    )


def register_user(base_url: str, user: User) -> User:
    """Register through the real /register form; return the user with its recovery code."""
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        form_page = client.get("/register")
        form_page.raise_for_status()
        match = _CSRF_INPUT_RE.search(form_page.text)
        assert match, "csrf_token not found on /register"
        resp = client.post(
            "/register",
            data={
                "username": user.username,
                "password": user.password,
                "confirm_password": user.password,
                "csrf_token": match.group(1),
            },
        )
        resp.raise_for_status()
        code = _RECOVERY_RE.search(resp.text)
        assert code, "registration did not reach recovery page"
        return replace(user, recovery_code=code.group(1).strip())


def login(page: Page, user: User) -> None:
    """Log in and wait for the app to settle (including today's opening)."""
    submit_login(page, user)
    wait_for_app(page)


def submit_login(page: Page, user: User) -> None:
    """Log in and return as soon as the diary loads, without waiting for streams."""
    page.goto("/login")
    page.locator('input[name="username"]').fill(user.username)
    page.locator('input[name="password"]').fill(user.password)
    page.get_by_role("button", name="Login").click()
    page.wait_for_url(re.compile(r"/d/"))


# --- page helpers -------------------------------------------------------------


def wait_for_app(page: Page) -> None:
    """Wait until the app shell has initialised and settled."""
    page.wait_for_function("() => Boolean(window.appInit && window.appInit.scroll)")
    wait_for_htmx_idle(page)
    wait_for_streams_idle(page)


def wait_for_streams_idle(page: Page) -> None:
    """Wait until no model reply (e.g. today's day opening) is still streaming.

    Live streams carry ``data-sse-url`` until they finalise. Leaving a page
    mid-stream abandons a generation server-side, so tests settle first.
    """
    page.wait_for_function(
        "() => !document.querySelector('response-stream[data-sse-url]')"
    )


def wait_for_htmx_idle(page: Page) -> None:
    """Wait until no htmx request is in flight."""
    page.wait_for_function("() => !document.querySelector('.htmx-request')")


# --- seeding over HTTP ----------------------------------------------------------

_BODY_CSRF_RE = re.compile(r'data-csrf-token="([^"]+)"')
_ENTRY_ID_RE = re.compile(r'id="entry-([0-9A-Za-z]+)"')


class ApiClient:
    """Drives the app's own endpoints with a logged-in session, for test setup.

    Seeding through the real routes keeps data encrypted exactly as it would be
    for a user; nothing is written to the database behind the app's back.
    """

    def __init__(self, base_url: str, storage_state_path: Path) -> None:
        state = json.loads(storage_state_path.read_text(encoding="utf-8"))
        cookies = httpx.Cookies()
        self._tz = "UTC"
        for cookie in state.get("cookies", []):
            cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
            if cookie["name"] == "tz":
                self._tz = unquote(cookie["value"]) or "UTC"
        self._client = httpx.Client(base_url=base_url, cookies=cookies, timeout=30.0)
        page = self._client.get("/d/today")
        page.raise_for_status()
        match = _BODY_CSRF_RE.search(page.text)
        assert match, "csrf token not found on /d/today; is the session logged in?"
        self._headers = {"X-CSRFToken": match.group(1), "HX-Request": "true"}

    def close(self) -> None:
        self._client.close()

    def create_entry(self, day: date, text: str) -> str:
        """Create a user entry at noon on ``day`` in the user's zone; return its id.

        The server files entries by ``user_time`` in the zone from the session's
        ``tz`` cookie, so noon *local* keeps the entry on ``day`` in any zone.
        """
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo(self._tz))
        resp = self._client.post(
            f"/e/{day.isoformat()}/entry",
            data={"text": text, "user_time": noon.isoformat()},
            headers=self._headers,
        )
        resp.raise_for_status()
        match = _ENTRY_ID_RE.search(resp.text)
        assert match, f"entry id not found in response: {resp.text[:200]}"
        return match.group(1)

    def start_reply(self, entry_id: str, day: date) -> int:
        """Start a model reply and keep watching it, like an open browser tab.

        The SSE stream is consumed on a background thread so several replies
        can be in flight at once; returns the stream's HTTP status as soon as
        the response starts. ``stop_replies`` (or completion) ends it.
        """
        started = threading.Event()
        status: list[int] = []
        url = f"/e/{day.isoformat()}/response/stream/{entry_id}"

        def watch() -> None:
            with httpx.Client(
                base_url=str(self._client.base_url),
                cookies=self._client.cookies,
                timeout=None,
            ) as client:
                with client.stream("GET", url) as resp:
                    status.append(resp.status_code)
                    started.set()
                    for _ in resp.iter_bytes():
                        pass

        threading.Thread(target=watch, name=f"reply-{entry_id}", daemon=True).start()
        started.wait(timeout=30)
        return status[0] if status else 0

    def stop_replies(self, entry_id: str) -> None:
        """Stop every in-flight reply to ``entry_id`` (the Stop button, per entry)."""
        self._client.post(f"/e/response/stop/{entry_id}", headers=self._headers)

    def add_tag(self, entry_id: str, tag: str) -> None:
        resp = self._client.post(
            f"/t/entry/{entry_id}", data={"tag": tag}, headers=self._headers
        )
        resp.raise_for_status()


def today_utc() -> date:
    # Browser contexts run with timezone_id="UTC", so "today" is the UTC date.
    return datetime.now(timezone.utc).date()


def marker(prefix: str = "kestrel") -> str:
    """A unique, searchable word to find a test's own data among shared data."""
    return f"{prefix}{secrets.token_hex(3)}"


def write_entry(page: Page, text: str) -> Locator:
    """Type an entry into today's form and submit it with Enter."""
    textarea = page.locator("#entry-text")
    expect(textarea).to_be_enabled()  # disabled while a stream is running
    textarea.fill(text)
    textarea.press("Enter")
    written = page.locator("#entries .entry.user", has_text=text)
    expect(written).to_be_visible()
    # Anchor on the id so the locator survives edits to the text.
    return page.locator(f"#entry-{written.get_attribute('data-entry-id')}")
