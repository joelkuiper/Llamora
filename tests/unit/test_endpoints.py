"""The one interpretation of upstream URLs and ``/v1`` (llamora.llm.endpoints)."""

from __future__ import annotations

import pytest

from llamora.llm.endpoints import (
    UpstreamEndpoints,
    api_path,
    resolve_endpoints,
    server_root,
)

LOCAL = UpstreamEndpoints(
    root="http://127.0.0.1:8081", api_base="http://127.0.0.1:8081/v1"
)
NOUS = UpstreamEndpoints(
    root="https://inference-api.nousresearch.com",
    api_base="https://inference-api.nousresearch.com/v1",
)


@pytest.mark.parametrize(
    "host",
    [
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8081/",
        "http://127.0.0.1:8081/v1",
        "http://127.0.0.1:8081/v1/",
        "http://127.0.0.1:8081/v1/chat/completions",
        "  http://127.0.0.1:8081  ",
    ],
)
def test_host_with_or_without_v1_is_the_same_server(host: str) -> None:
    assert resolve_endpoints(host=host) == LOCAL


@pytest.mark.parametrize(
    "host",
    [
        "https://inference-api.nousresearch.com",
        "https://inference-api.nousresearch.com/v1",
    ],
)
def test_hosted_provider_given_as_host(host: str) -> None:
    assert resolve_endpoints(host=host) == NOUS


@pytest.mark.parametrize(
    ("provider_base", "expected"),
    [
        (
            "https://api.openai.com/v1",
            UpstreamEndpoints(
                root="https://api.openai.com", api_base="https://api.openai.com/v1"
            ),
        ),
        (
            "https://api.openai.com/v1/chat/completions",
            UpstreamEndpoints(
                root="https://api.openai.com", api_base="https://api.openai.com/v1"
            ),
        ),
        (
            "https://openrouter.ai/api/v1/",
            UpstreamEndpoints(
                root="https://openrouter.ai/api",
                api_base="https://openrouter.ai/api/v1",
            ),
        ),
        # Not every provider's API base ends in /v1: it is used verbatim.
        (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            UpstreamEndpoints(
                root="https://generativelanguage.googleapis.com/v1beta/openai",
                api_base="https://generativelanguage.googleapis.com/v1beta/openai",
            ),
        ),
    ],
)
def test_base_url_is_the_api_base_as_documented(
    provider_base: str, expected: UpstreamEndpoints
) -> None:
    # (Not named ``base_url``: that is a pytest-base-url fixture.)
    assert resolve_endpoints(base_url=provider_base) == expected


def test_host_for_probes_and_base_url_for_chat_when_both_are_set() -> None:
    resolved = resolve_endpoints(
        host="http://127.0.0.1:8081/v1", base_url="https://proxy.example/v1"
    )
    assert resolved == UpstreamEndpoints(
        root="http://127.0.0.1:8081", api_base="https://proxy.example/v1"
    )


@pytest.mark.parametrize(
    ("endpoint", "path"),
    [
        (None, "/v1"),
        ("/v1/chat/completions", "/v1"),
        ("v1/chat/completions", "/v1"),
        ("/api/v2/chat/completions", "/api/v2"),
        ("/chat/completions", "/v1"),
    ],
)
def test_custom_chat_endpoint_sets_the_api_path(
    endpoint: str | None, path: str
) -> None:
    assert api_path(endpoint) == path
    assert (
        resolve_endpoints(host="http://h:1", endpoint=endpoint).api_base
        == f"http://h:1{path}"
    )


def test_server_root_strips_only_a_trailing_v1() -> None:
    assert server_root("http://h/v1beta") == "http://h/v1beta"
    assert server_root("http://h/v1/extra") == "http://h/v1/extra"


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_nothing_configured_is_an_error(empty: str | None) -> None:
    with pytest.raises(ValueError, match="LLM.upstream.host"):
        resolve_endpoints(host=empty, base_url=empty)
