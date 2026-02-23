from __future__ import annotations

import os
import re
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


DEFAULT_UPSTREAM_CONFIG: dict[str, Any] = {
    "host": "",
    "parallel": 1,
    "ctx_size": 8192,
    "health_ttl": 10.0,
    "skip_health_check": False,
}

DEFAULT_LLM_GENERATION: dict[str, Any] = {
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
        "workers": 1,
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
        "entry_index_max_elements": 100_000,
        "entry_index_allow_growth": False,
        "stream_global_memory_budget_bytes": 32 * 1024 * 1024,
        "progressive_inline_backfill": True,
        "include_index_coverage_hints": False,
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
    },
    "SESSION": {
        "ttl": 7 * 24 * 60 * 60,
    },
    "DATABASE": {
        "path": "state.sqlite3",
        "pool_size": 25,
        "pool_acquire_timeout": 10,
        "timeout": 5.0,
        "busy_timeout": 5000,
        "mmap_size": 10 * 1024 * 1024,
    },
    "MIGRATIONS": {
        "path": "migrations",
        "baseline_version": 1,
    },
    "EMBEDDING": {
        "model": "BAAI/bge-small-en-v1.5",
        "concurrency": _cpu_count(),
        "global_memory_budget_bytes": 256 * 1024 * 1024,
        "chunking": {
            "max_chars": 1200,
            "overlap_chars": 200,
        },
        "index": {
            "backfill_batch_size": 64,
            "backfill_max_users_per_tick": 8,
            "backfill_wall_budget_ms": 40.0,
            "backfill_cpu_budget_ms": 40.0,
            "coverage_recent_limit": 1000,
            "coverage_emit_interval_s": 30.0,
        },
    },
    "LLM": {
        "upstream": {
            **DEFAULT_UPSTREAM_CONFIG,
        },
        "generation": DEFAULT_LLM_GENERATION,
        "stream": {
            "pending_ttl": 300,
            "queue_limit": 4,
            "repeat_guard_size": 6,
            "repeat_guard_min_length": 12,
        },
        "allowed_config_keys": ["temperature"],
        "tokenizer": {
            "encoding": "cl100k_base",
            "safety_margin": {
                "ratio": 0.1,
                "min_tokens": 128,
            },
        },
        "chat": {
            "endpoint": "/v1/chat/completions",
            "model": "local",
            "api_key": None,
            "base_url": None,
            "timeout_seconds": 30.0,
            "max_retries": 2,
            "parameter_allowlist": [
                "top_k",
                "n_keep",
                "cache_prompt",
                "mirostat",
                "mirostat_tau",
                "mirostat_eta",
                "repeat_penalty",
                "repeat_last_n",
                "penalize_nl",
            ],
            "parameters": {},
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
    "PROXY": {
        "trusted_hops": 0,
    },
    "CRYPTO": {
        "dek_storage": "session",
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


_BYTE_SIZE_UNITS: dict[str, int] = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def _parse_byte_size(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return max(int(value), 0)

    raw = str(value).strip()
    if not raw:
        return default

    if raw.isdigit():
        return max(int(raw), 0)

    match = re.fullmatch(r"(?i)\s*(\d+(?:\.\d+)?)\s*([kmgt]i?b)\s*", raw)
    if not match:
        return default

    amount = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = _BYTE_SIZE_UNITS.get(unit)
    if multiplier is None:
        return default
    return max(int(amount * multiplier), 0)


def _normalise_byte_budgets() -> None:
    search_default = int(DEFAULTS["SEARCH"]["stream_global_memory_budget_bytes"])
    embedding_default = int(DEFAULTS["EMBEDDING"]["global_memory_budget_bytes"])

    stream_budget = _parse_byte_size(
        settings.get("SEARCH.stream_global_memory_budget_bytes"),
        default=search_default,
    )
    embedding_budget = _parse_byte_size(
        settings.get("EMBEDDING.global_memory_budget_bytes"),
        default=embedding_default,
    )

    settings.set("SEARCH.stream_global_memory_budget_bytes", stream_budget)
    settings.set("EMBEDDING.global_memory_budget_bytes", embedding_budget)


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
_normalise_byte_budgets()


generation_overrides = _normalise_mapping_keys(
    _coerce_mapping(settings.get("LLM.generation", {}))
)
settings.set(
    "LLM.generation",
    {
        **DEFAULT_LLM_GENERATION,
        **generation_overrides,
    },
)

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
