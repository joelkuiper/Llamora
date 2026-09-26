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
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from fake_llm import FakeLLM
from harness import (
    ApiClient,
    LiveServer,
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
def app_page(new_context: Callable[..., BrowserContext], user_state: Path) -> Page:
    """A logged-in page on /d/today, in its own browser context.

    Built on pytest-playwright's ``new_context`` so tracing/screenshots apply.
    """
    context = new_context(storage_state=str(user_state))
    page = context.new_page()
    page.goto("/d/today")
    wait_for_app(page)
    return page
