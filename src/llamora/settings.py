from __future__ import annotations

import os
import base64
import binascii
import secrets
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from dynaconf import Dynaconf

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent


def _resolve_config_dir() -> Path:
    env_override = os.environ.get("LLAMORA_CONFIG_DIR")
    candidates: list[Path] = []

    if env_override:
        candidates.append(Path(env_override).expanduser())

    candidates.append(PROJECT_ROOT / "config")
    candidates.append(PROJECT_ROOT.parent / "config")

    for candidate in candidates:
        expanded = candidate.expanduser()
        if expanded.is_dir():
            return expanded.resolve()

    searched = ", ".join(str(path) for path in candidates)
    message = f"Unable to locate configuration directory. Searched: {searched}."
    if env_override:
        message += " Set LLAMORA_CONFIG_DIR to a valid directory."
    raise RuntimeError(message)


CONFIG_DIR = _resolve_config_dir()


def _cpu_count(default: int = 4) -> int:
    count = os.cpu_count() or default
    return max(count, 1)


DEFAULT_LLM_ARGS: dict[str, Any] = {
    "server": True,
    "nobrowser": True,
    "threads": _cpu_count(),
    "n_gpu_layers": 999,
    "gpu": "auto",
    "ctx_size": 8192,
}

DEFAULT_LLM_REQUEST: dict[str, Any] = {
    "n_predict": 1024,
    "stream": True,
    "stop": ["<|im_start|>", "<|im_end|>", "<|endoftext|>", "<|end|>"],
    "n_keep": -1,
    "cache_prompt": True,
}

DEFAULTS: dict[str, Any] = {
    "APP_NAME": "Llamora",
    "SECRET_KEY": None,
    "LOG_LEVEL": "INFO",
    "APP": {
        "host": "127.0.0.1",
        "port": 5000,
    },
    "FEATURES": {
        "disable_registration": False,
    },
    "LIMITS": {
        "max_tag_length": 64,
        "max_username_length": 30,
        "max_password_length": 128,
        "min_password_length": 8,
        "max_message_length": 1000,
        "max_search_query_length": 512,
    },
    "SEARCH": {
        "recent_limit": 50,
        "recent_suggestion_limit": 8,
        "message_index_max_elements": 100_000,
        "progressive": {
            "k1": 128,
            "k2": 10,
            "rounds": 3,
            "batch_size": 1000,
            "max_ms": 1500,
            "poor_match_max_cos": 0.28,
            "poor_match_min_hits": 3,
        },
    },
    "AUTH": {
        "max_login_attempts": 5,
        "login_lockout_ttl": 15 * 60,
        "login_failure_cache_size": 2048,
    },
    "SESSION": {
        "ttl": 7 * 24 * 60 * 60,
    },
    "MESSAGES": {
        "history_cache": {
            "maxsize": 256,
            "ttl": 60,
        }
    },
    "DATABASE": {
        "path": "state.sqlite3",
        "pool_size": 25,
        "pool_acquire_timeout": 10,
        "timeout": 5.0,
        "busy_timeout": 5000,
        "mmap_size": 10 * 1024 * 1024,
    },
    "EMBEDDING": {
        "model": "BAAI/bge-small-en-v1.5",
        "concurrency": _cpu_count(),
    },
    "LLM": {
        "server": {
            "llamafile_path": "",
            "host": None,
            "args": DEFAULT_LLM_ARGS,
        },
        "request": DEFAULT_LLM_REQUEST,
        "stream": {
            "pending_ttl": 300,
            "queue_limit": 4,
            "repeat_guard_size": 6,
            "repeat_guard_min_length": 12,
        },
        "allowed_config_keys": ["temperature"],
        "response_kinds": [
            {
                "id": "reply",
                "label": "Reply",
                "prompt": "Respond to the user's message with a calm, grounded reply.",
            },
            {
                "id": "reflect",
                "label": "Reflect",
                "prompt": "Offer a gentle reflection and emotional acknowledgement of the message.",
            },
            {
                "id": "summarize",
                "label": "Summarize",
                "prompt": "Provide a concise, factual summary of the message.",
            },
        ],
        "tokenizer": {
            "model": "Qwen/Qwen3-4B-Instruct-2507",
            "trust_remote_code": True,
        },
    },
    "PROMPTS": {
        "template_dir": str(PACKAGE_DIR / "llm" / "templates"),
    },
    "UI": {
        "clock_format": "24h",
    },
    "COOKIES": {
        "name": "llamora",
        "secret": None,
    },
    "CRYPTO": {
        "dek_storage": "cookie",
    },
    "WORKERS": {
        "index_worker": {
            "max_queue_size": 1024,
            "batch_size": 32,
            "flush_interval": 0.05,
        }
    },
}

settings = Dynaconf(
    envvar_prefix="LLAMORA",
    settings_files=[
        CONFIG_DIR / "settings.toml",
        CONFIG_DIR / ".secrets.toml",
        CONFIG_DIR / "settings.local.toml",
    ],
    environments=True,
    env_switcher="LLAMORA_ENV",
    load_dotenv=True,
    envvar_parse_values=True,
    merge_enabled=True,
    defaults=DEFAULTS,
)


_MISSING = object()


def _ensure_defaults(prefix: str, defaults: dict[str, Any]) -> None:
    for key, value in defaults.items():
        dotted = f"{prefix}.{key}" if prefix else key
        existing = settings.get(dotted, _MISSING)

        if isinstance(value, dict):
            if existing is _MISSING:
                settings.set(dotted, value.copy())
                existing = settings.get(dotted, _MISSING)
                # Only recurse when the stored value looks like a mapping to avoid
                # clobbering user-provided primitives.
            if isinstance(existing, Mapping):
                _ensure_defaults(dotted, value)
            continue

        if existing is _MISSING:
            settings.set(dotted, value)


_ensure_defaults("", DEFAULTS)


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _normalise_mapping_keys(data: dict[str, Any]) -> dict[str, Any]:
    normalised: dict[str, Any] = {}
    for key, value in data.items():
        key_str = str(key).replace("-", "_").lower()
        if isinstance(value, Mapping):
            value = _normalise_mapping_keys(_coerce_mapping(value))
            normalised[key_str] = value
    return normalised


def _normalise_secret_key() -> None:
    secret = settings.get("SECRET_KEY")
    if secret:
        settings.set("SECRET_KEY", str(secret))
        return

    generated = secrets.token_urlsafe(32)
    settings.set("SECRET_KEY", generated)


def _normalise_cookie_secret() -> None:
    raw_secret = settings.get("COOKIES.secret")
    secret_text = str(raw_secret or "")

    if not secret_text:
        generated = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8")
        settings.set("COOKIES.secret", generated)
        return

    try:
        decoded = base64.b64decode(secret_text, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(
            "Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string"
        ) from exc

    if len(decoded) != 32:
        raise RuntimeError("Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string")


_normalise_secret_key()
_normalise_cookie_secret()


server_args = _normalise_mapping_keys(
    _coerce_mapping(settings.get("LLM.server.args", {}))
)
settings.set(
    "LLM.server.args",
    {
        **DEFAULT_LLM_ARGS,
        **server_args,
    },
)

request_overrides = _normalise_mapping_keys(
    _coerce_mapping(settings.get("LLM.request", {}))
)
settings.set(
    "LLM.request",
    {
        **DEFAULT_LLM_REQUEST,
        **request_overrides,
    },
)

threads = int(settings.get("LLM.server.args.threads", 0))
if threads <= 0:
    settings.set("LLM.server.args.threads", _cpu_count())

# Normalise a few derived values that are used by the Quart application.
settings.set(
    "SESSION.permanent_lifetime",
    timedelta(seconds=int(settings.get("SESSION.ttl", 0))),
)
settings.set(
    "SESSION.csrf_time_limit",
    int(settings.get("SESSION.ttl", 0)),
)
embedding_concurrency = int(settings.get("EMBEDDING.concurrency", 0))
if embedding_concurrency <= 0:
    embedding_concurrency = _cpu_count()
    settings.set("EMBEDDING.concurrency", embedding_concurrency)

queue_size_default = DEFAULTS["WORKERS"]["index_worker"]["max_queue_size"]
queue_size_raw = settings.get("WORKERS.index_worker.max_queue_size", queue_size_default)
try:
    queue_size = int(queue_size_raw)
except (TypeError, ValueError):
    queue_size = queue_size_default

settings.set("WORKERS.index_worker.max_queue_size", queue_size)

__all__ = ["settings"]
