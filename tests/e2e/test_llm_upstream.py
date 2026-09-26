"""Talking to a key-protected, OpenAI-compatible model server.

The API key (``LLM.chat.api_key``) must be sent as ``Authorization: Bearer``
on every upstream call, chat and probes alike, and the configured model name
(``LLM.chat.model``) must be in every chat payload. Both can come from the
settings files or from ``LLAMORA_LLM__CHAT__*`` environment variables.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Page, expect

from fake_llm import FakeLLM
from harness import (
    LiveServer,
    new_credentials,
    register_user,
    start_server,
    submit_login,
    wait_for_app,
    write_entry,
)

API_KEY = "sk-test-0123456789"
MODEL = "llamora-test-model"


@pytest.fixture(scope="module")
def keyed_llm() -> Iterator[FakeLLM]:
    """A model server that, like ``llama-server --api-key``, demands the key."""
    fake = FakeLLM(api_key=API_KEY).start()
    yield fake
    fake.stop()


@pytest.fixture
def keyed_server(
    keyed_llm: FakeLLM, tmp_path_factory: pytest.TempPathFactory, assets: None
) -> Callable[..., LiveServer]:
    servers: list[LiveServer] = []

    def start(
        *,
        extra_config: str = "",
        extra_env: dict[str, str] | None = None,
        host_suffix: str = "",
        hosted: bool = False,
    ):
        keyed_llm.reset()
        keyed_llm.api_key = API_KEY
        keyed_llm.llamacpp_endpoints = not hosted
        server = start_server(
            tmp_path_factory.mktemp("keyed"),
            llm_url=keyed_llm.url + host_suffix,
            extra_config=extra_config,
            extra_env=extra_env,
        )
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.stop()


def diary_on(server: LiveServer, open_page: Callable[..., Page]) -> Page:
    user = register_user(server.url, new_credentials("keyed"))
    page = open_page(base_url=server.url)
    submit_login(page, user)
    return page


def chat_calls(fake: FakeLLM) -> list[tuple[str, str, str | None]]:
    return [call for call in fake.seen if call[1].endswith("/chat/completions")]


def expect_key_and_model_everywhere(fake: FakeLLM) -> None:
    bearer = f"Bearer {API_KEY}"
    props = [call for call in fake.seen if call[1] == "/props"]
    assert props and all(auth == bearer for _, _, auth in props), props
    calls = chat_calls(fake)
    assert calls and all(auth == bearer for _, _, auth in calls), calls
    assert {request.get("model") for request in fake.requests} == {MODEL}


def test_key_and_model_from_settings_reach_every_upstream_call(
    keyed_server, keyed_llm: FakeLLM, open_page: Callable[..., Page]
) -> None:
    server = keyed_server(
        extra_config=f'\n[default.LLM.chat]\napi_key = "{API_KEY}"\nmodel = "{MODEL}"\n'
    )
    page = diary_on(server, open_page)
    wait_for_app(page)
    keyed_llm.reply = "A reply through the keyed server."

    entry = write_entry(page, "Does the key get through?")
    entry.get_by_role("button", name="Respond").click()
    entry_id = entry.get_attribute("data-entry-id")
    expect(page.locator(f"#entry-responses-{entry_id}")).to_contain_text(
        keyed_llm.reply
    )
    wait_for_app(page)
    # Tag suggestions use the non-streaming (structured output) call.
    entry.get_by_role("button", name="Add traces").click()
    popover = page.locator("#tag-popover-global")
    expect(
        popover.locator(f'.tag-suggestion[data-tag="{keyed_llm.tags[0]}"]')
    ).to_be_visible()

    assert keyed_llm.chat_requests(structured=True), "no structured call was made"
    expect_key_and_model_everywhere(keyed_llm)


def test_key_and_model_from_environment_variables(
    keyed_server, keyed_llm: FakeLLM, open_page: Callable[..., Page]
) -> None:
    server = keyed_server(
        extra_env={
            "LLAMORA_LLM__CHAT__API_KEY": API_KEY,
            "LLAMORA_LLM__CHAT__MODEL": MODEL,
        }
    )
    keyed_llm.reply = "An opening through the keyed server."

    page = diary_on(server, open_page)

    expect(page.locator("#entries .entry--opening")).to_contain_text(keyed_llm.reply)
    wait_for_app(page)
    expect_key_and_model_everywhere(keyed_llm)


def test_missing_key_is_reported_instead_of_hanging(
    keyed_server, keyed_llm: FakeLLM, open_page: Callable[..., Page]
) -> None:
    server = keyed_server()  # no key configured; the model server demands one

    page = diary_on(server, open_page)

    opening = page.locator("#entries .entry--opening")
    expect(opening).to_have_class(re.compile(r"\bentry--error\b"), timeout=15_000)
    wait_for_app(page)
    assert all(auth != f"Bearer {API_KEY}" for _, _, auth in chat_calls(keyed_llm))


def probes(fake: FakeLLM, path: str) -> int:
    return sum(1 for _, seen_path, _ in fake.seen if seen_path == path)


def test_hosted_api_without_health_or_props(
    keyed_server, keyed_llm: FakeLLM, open_page: Callable[..., Page]
) -> None:
    # Mirrors: LLAMORA_LLM__UPSTREAM__HOST=https://provider.example/v1 plus key
    # and model, against a provider that has neither /health nor /props.
    server = keyed_server(
        host_suffix="/v1",
        hosted=True,
        extra_env={
            "LLAMORA_LLM__CHAT__API_KEY": API_KEY,
            "LLAMORA_LLM__CHAT__MODEL": MODEL,
        },
    )
    keyed_llm.reply = "Hello from a hosted model."

    page = diary_on(server, open_page)
    expect(page.locator("#entries .entry--opening")).to_contain_text(keyed_llm.reply)
    wait_for_app(page)
    entry = write_entry(page, "A hosted reply, please")
    entry.get_by_role("button", name="Respond").click()
    entry_id = entry.get_attribute("data-entry-id")
    expect(page.locator(f"#entry-responses-{entry_id}")).to_contain_text(
        keyed_llm.reply
    )
    wait_for_app(page)

    assert {path for _, path, _ in chat_calls(keyed_llm)} == {"/v1/chat/completions"}
    assert {request.get("model") for request in keyed_llm.requests} == {MODEL}
    # Missing endpoints are noticed once, not probed on every request.
    assert probes(keyed_llm, "/health") <= 1
    assert probes(keyed_llm, "/props") <= 1


def test_llamacpp_still_loading_is_reported(
    keyed_server, keyed_llm: FakeLLM, open_page: Callable[..., Page]
) -> None:
    server = keyed_server(extra_config=f'\n[default.LLM.chat]\napi_key = "{API_KEY}"\n')
    keyed_llm.health_status = 503  # llama.cpp answers 503 while loading a model

    page = diary_on(server, open_page)

    expect(page.locator("#entries .entry--opening")).to_have_class(
        re.compile(r"\bentry--error\b"), timeout=15_000
    )
    wait_for_app(page)
    assert chat_calls(keyed_llm) == [], "chatted with a model that is still loading"
