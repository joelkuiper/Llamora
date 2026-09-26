"""Fixtures for browser end-to-end tests.

Fixture graph (session scoped unless noted):

    fake_llm ─ live_server ─ base_url ─ user ─ user_state ─ app_page (function)

Every xdist worker gets its own fake LLM, server, database and users; the
prod asset bundle is built once per run (see the ``assets`` fixture).

``page`` (from pytest-playwright) is an unauthenticated page; ``app_page`` is
logged in as the shared session user and already on /d/today. ``api`` seeds
data for that same user through the app's own HTTP endpoints.
"""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Iterator
from pathlib import Path
from datetime import datetime
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from fake_llm import FakeLLM
from timekit import Diary
from harness import (
    ApiClient,
    LiveServer,
    submit_login,
    User,
    build_assets,
    login,
    new_credentials,
    register_user,
    start_server,
    wait_for_app,
)

E2E_DIR = Path(__file__).resolve().parent


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--e2e-no-build",
        action="store_true",
        help="Skip the prod asset build before e2e tests (use frontend/dist as-is).",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if E2E_DIR in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.e2e)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    report = yield
    server: LiveServer | None = getattr(item.config, "_llamora_server", None)
    if report.failed and server is not None:
        report.sections.append(("Llamora server log (tail)", server.log_tail()))
    return report


@pytest.fixture(scope="session")
def assets(
    pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Build the prod bundle once per run, even with several xdist workers.

    Workers share the run's temp root (``basetemp.parent`` under xdist), so a
    lock there lets the first worker build while the others wait and skip.
    """
    if pytestconfig.getoption("--e2e-no-build"):
        return
    basetemp = tmp_path_factory.getbasetemp()
    run_root = basetemp.parent if hasattr(pytestconfig, "workerinput") else basetemp
    built = run_root / "e2e-assets.built"
    with (run_root / "e2e-assets.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not built.exists():
            build_assets()
            built.touch()


@pytest.fixture(scope="session")
def fake_llm() -> Iterator[FakeLLM]:
    fake = FakeLLM().start()
    yield fake
    fake.stop()


@pytest.fixture(autouse=True)
def _reset_fake_llm(fake_llm: FakeLLM) -> None:
    fake_llm.reset()


@pytest.fixture(scope="session")
def live_server(
    pytestconfig: pytest.Config,
    tmp_path_factory: pytest.TempPathFactory,
    fake_llm: FakeLLM,
    assets: None,
) -> Iterator[LiveServer]:
    server = start_server(tmp_path_factory.mktemp("llamora"), llm_url=fake_llm.url)
    pytestconfig._llamora_server = server  # type: ignore[attr-defined]
    yield server
    server.stop()


@pytest.fixture(scope="session")
def base_url(live_server: LiveServer) -> str:
    # Overrides pytest-base-url so page.goto("/d/today") resolves to the server.
    return live_server.url


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {
        **browser_context_args,
        "locale": "en-US",
        "timezone_id": "UTC",
        "viewport": {"width": 1280, "height": 900},
    }


@pytest.fixture(scope="session")
def make_user(base_url: str) -> Callable[..., User]:
    """Factory: register a fresh user over HTTP. Use when a test needs isolation."""

    def factory(prefix: str = "e2e") -> User:
        return register_user(base_url, new_credentials(prefix))

    return factory


@pytest.fixture(scope="session")
def user(make_user: Callable[..., User]) -> User:
    """The shared session user. Its data accumulates across tests."""
    return make_user("shared")


@pytest.fixture(scope="session")
def user_state(
    browser: Browser,
    browser_context_args: dict[str, Any],
    user: User,
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Log in once through the UI and reuse the storage state everywhere."""
    state_path = tmp_path_factory.mktemp("auth") / "user.json"
    context = browser.new_context(**browser_context_args)
    try:
        login(context.new_page(), user)
        context.storage_state(path=str(state_path))
    finally:
        context.close()
    return state_path


@pytest.fixture(scope="session")
def api(base_url: str, user_state: Path) -> Iterator[ApiClient]:
    """HTTP client logged in as the shared user, for seeding test data."""
    client = ApiClient(base_url, user_state)
    yield client
    client.close()


@pytest.fixture
def fresh_session(
    new_context: Callable[..., BrowserContext],
    make_user: Callable[..., User],
    base_url: str,
    tmp_path: Path,
) -> Iterator[tuple[Page, ApiClient]]:
    """A brand-new user: (logged-in page on /d/today, seeding client).

    For tests whose assertions depend on *all* of a user's data (search
    result counts, empty states), so the shared user's entries don't leak in.
    """
    context = new_context()
    page = context.new_page()
    login(page, make_user("fresh"))
    state_path = tmp_path / "fresh-user.json"
    context.storage_state(path=str(state_path))
    client = ApiClient(base_url, state_path)
    yield page, client
    client.close()


@pytest.fixture
def open_page(
    browser: Browser, browser_context_args: dict[str, Any]
) -> Iterator[Callable[..., Page]]:
    """Factory: a page in a new context with its own zone, clock and session.

    pytest-playwright's ``new_context`` can't override ``base_url`` or
    ``timezone_id`` per call, so these contexts are built on the browser
    directly (and closed here); they don't get its automatic failure traces.
    """
    contexts: list[BrowserContext] = []

    def factory(
        *,
        base_url: str,
        tz: str = "UTC",
        at: datetime | None = None,
        storage_state: str | None = None,
    ) -> Page:
        args = {**browser_context_args, "base_url": base_url, "timezone_id": tz}
        if storage_state is not None:
            args["storage_state"] = storage_state
        context = browser.new_context(**args)
        contexts.append(context)
        page = context.new_page()
        if at is not None:
            page.clock.install(time=at)
        return page

    yield factory
    for context in contexts:
        context.close()


@pytest.fixture
def diary_at(
    open_page: Callable[..., Page],
    fake_llm: FakeLLM,
    live_server: LiveServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Callable[..., Diary]]:
    """Factory: a fresh user logged in with a pinned browser zone and clock.

    ``tz`` sets the browser's IANA zone; ``at`` (an aware datetime) installs
    Playwright's clock, which then keeps running from there. ``server_now``
    starts a dedicated server whose clock starts at that instant, for
    server/client divergence. ``settle=False`` returns as soon as the diary
    loads, without waiting for the day opening to finish streaming.
    """
    servers: list[LiveServer] = []
    clients: list[ApiClient] = []

    def open_diary(
        *,
        tz: str = "UTC",
        at: datetime | None = None,
        server_now: datetime | None = None,
        settle: bool = True,
    ) -> Diary:
        base_url = live_server.url
        if server_now is not None:
            server = start_server(
                tmp_path_factory.mktemp("clocked"), llm_url=fake_llm.url, now=server_now
            )
            servers.append(server)
            base_url = server.url
        user = register_user(base_url, new_credentials("clock"))
        page = open_page(base_url=base_url, tz=tz, at=at)
        submit_login(page, user)
        if settle:
            wait_for_app(page)
        state = tmp_path_factory.mktemp("state") / "user.json"
        page.context.storage_state(path=str(state))
        api = ApiClient(base_url, state)
        clients.append(api)
        return Diary(page=page, user=user, api=api, base_url=base_url, tz=tz)

    yield open_diary
    for client in clients:
        client.close()
    for server in servers:
        server.stop()


@pytest.fixture
def app_page(new_context: Callable[..., BrowserContext], user_state: Path) -> Page:
    """A logged-in page on /d/today, in its own browser context.

    Built on pytest-playwright's ``new_context`` so tracing/screenshots apply.
    """
    context = new_context(storage_state=str(user_state))
    page = context.new_page()
    page.goto("/d/today")
    wait_for_app(page)
    return page
