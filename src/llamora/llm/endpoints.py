"""Where the model server lives: one set of rules for URLs and ``/v1``.

Two settings can point at the server, and people write both with and without
``/v1``. Everything that talks to the upstream resolves them here, so there
is exactly one interpretation:

``LLM.upstream.host``
    The server *root*, e.g. ``http://127.0.0.1:8081`` or
    ``https://inference-api.nousresearch.com``. A trailing ``/v1`` (or the
    full ``/v1/chat/completions``) is tolerated and removed.

``LLM.chat.base_url``
    The OpenAI API base URL exactly as a provider documents it, e.g.
    ``https://api.openai.com/v1`` or
    ``https://generativelanguage.googleapis.com/v1beta/openai``. Used as-is
    for chat (a trailing ``/chat/completions`` is tolerated and removed).

``LLM.chat.endpoint``
    The chat completions path under the root, default
    ``/v1/chat/completions``. This is the only place ``/v1`` is defined: the
    API base for a host is ``root + endpoint`` minus ``/chat/completions``.

With only a host, chat goes to ``host + /v1``. With only a base URL, the
llama.cpp probes (``/health``, ``/props``) go to its root: the base URL minus
a trailing ``/v1``. With both, the host is used for probes and the base URL
for chat.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CHAT_ENDPOINT = "/v1/chat/completions"
_CHAT_SUFFIX = "/chat/completions"
_API_VERSION = "/v1"


@dataclass(frozen=True, slots=True)
class UpstreamEndpoints:
    root: str
    """Server root, for llama.cpp's optional ``/health`` and ``/props``."""

    api_base: str
    """OpenAI API base for the SDK; it appends ``/chat/completions``."""


def _trim(url: str | None) -> str:
    return str(url or "").strip().rstrip("/")


def _without_suffix(url: str, suffix: str) -> str:
    return url[: -len(suffix)].rstrip("/") if url.endswith(suffix) else url


def normalize_chat_endpoint(endpoint: str | None) -> str:
    """The chat completions path, always starting with ``/``."""
    path = str(endpoint or DEFAULT_CHAT_ENDPOINT).strip() or DEFAULT_CHAT_ENDPOINT
    return path if path.startswith("/") else f"/{path}"


def api_path(endpoint: str | None = None) -> str:
    """The API base path under the root, e.g. ``/v1`` for the default endpoint."""
    path = _without_suffix(normalize_chat_endpoint(endpoint), _CHAT_SUFFIX)
    return path or _API_VERSION


def server_root(url: str | None) -> str:
    """A server root from a URL written with or without ``/v1`` (or the endpoint)."""
    return _without_suffix(_without_suffix(_trim(url), _CHAT_SUFFIX), _API_VERSION)


def resolve_endpoints(
    *,
    host: str | None = None,
    base_url: str | None = None,
    endpoint: str | None = None,
) -> UpstreamEndpoints:
    """Resolve the configured host/base URL into a probe root and an API base."""
    base = _without_suffix(_trim(base_url), _CHAT_SUFFIX)
    root = server_root(host) or server_root(base)
    if not root:
        raise ValueError(
            "Configure the model server: set LLM.upstream.host "
            "(LLAMORA_LLM__UPSTREAM__HOST) or LLM.chat.base_url."
        )
    return UpstreamEndpoints(root=root, api_base=base or f"{root}{api_path(endpoint)}")
