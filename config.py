import json
import os
import logging
from copy import deepcopy

MAX_USERNAME_LENGTH = 30
MAX_PASSWORD_LENGTH = 128
MAX_MESSAGE_LENGTH = 1000
MAX_SESSION_NAME_LENGTH = 100
APP_NAME = "Llamora"


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _json_env(name: str):
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logging.warning("Invalid JSON in %s, ignoring.", name)
        return None


# Sensible defaults
DEFAULT_LLAMA_ARGS = {
    "server": True,
    "nobrowser": True,
    "threads": os.cpu_count() or 4,
    "n_gpu_layers": 999,
    "gpu": "auto",
    "ctx_size": 8192,  # n_ctx
}

env_overrides = _json_env("LLAMORA_LLAMA_ARGS")

LLM_SERVER = {
    "llamafile_path": os.getenv("LLAMORA_LLAMAFILE", ""),
    "host": os.getenv("LLAMORA_LLAMA_HOST"),
    "args": _deep_merge(DEFAULT_LLAMA_ARGS, env_overrides or {}),
}

llm_request_overrides = _json_env("LLAMORA_LLM_REQUEST") or {}


DEFAULT_LLM_REQUEST = {
    "n_predict": 1024,
    "stream": True,
    "stop": ["<|end|>", "<|assistant|>"],
    **llm_request_overrides,
}
