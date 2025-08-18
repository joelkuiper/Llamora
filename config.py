import orjson
import os
import logging
from copy import deepcopy
from datetime import timedelta
from util import str_to_bool

MAX_USERNAME_LENGTH = 30
MAX_PASSWORD_LENGTH = 128
MIN_PASSWORD_LENGTH = 8
MAX_MESSAGE_LENGTH = 1000
MAX_SESSION_NAME_LENGTH = 100
APP_NAME = "Llamora"

# Feature toggles
DISABLE_REGISTRATION = str_to_bool(os.getenv("LLAMORA_DISABLE_REGISTRATION", "false"))

# Embedding configuration
EMBED_MODEL = os.getenv("LLAMORA_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# Session and CSRF configuration
SESSION_TTL = int(os.getenv("LLAMORA_SESSION_TTL", 7 * 24 * 60 * 60))
PERMANENT_SESSION_LIFETIME = timedelta(seconds=SESSION_TTL)
WTF_CSRF_TIME_LIMIT = SESSION_TTL

# Database pool and connection defaults
DB_POOL_SIZE = int(os.getenv("LLAMORA_DB_POOL_SIZE", 25))
DB_POOL_ACQUIRE_TIMEOUT = float(os.getenv("LLAMORA_DB_ACQUIRE_TIMEOUT", 10))
DB_TIMEOUT = float(os.getenv("LLAMORA_DB_TIMEOUT", 5))
DB_BUSY_TIMEOUT = int(os.getenv("LLAMORA_DB_BUSY_TIMEOUT", 5000))  # milliseconds
DB_MMAP_SIZE = int(os.getenv("LLAMORA_DB_MMAP_SIZE", 10 * 1024 * 1024))


# Only allow a limited subset of parameters to be forwarded to the LLM
ALLOWED_LLM_CONFIG_KEYS = {"temperature"}


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
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
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
    "logit_bias": [[426, -1.0], [[500, -1.0]]],  # Reduce {, } likelihood
    **llm_request_overrides,
}

# Prompt and grammar files
PROMPT_FILE = os.getenv(
    "LLAMORA_PROMPT_FILE",
    os.path.join(
        os.path.dirname(__file__), "llm", "prompts", "llamora_phi.j2"
    ),
)
GRAMMAR_FILE = os.getenv(
    "LLAMORA_GRAMMAR_FILE",
    os.path.join(os.path.dirname(__file__), "llm", "meta_grammar.bnf"),
)

# Progressive backfill search defaults
PROGRESSIVE_K1 = int(os.getenv("LLAMORA_PROGRESSIVE_K1", 128))
PROGRESSIVE_K2 = int(os.getenv("LLAMORA_PROGRESSIVE_K2", 10))
PROGRESSIVE_ROUNDS = int(os.getenv("LLAMORA_PROGRESSIVE_ROUNDS", 3))
PROGRESSIVE_BATCH = int(os.getenv("LLAMORA_PROGRESSIVE_BATCH", 1000))
PROGRESSIVE_MAX_MS = int(os.getenv("LLAMORA_PROGRESSIVE_MAX_MS", 1500))
POOR_MATCH_MAX_COS = float(os.getenv("LLAMORA_POOR_MATCH_MAX_COS", 0.28))
POOR_MATCH_MIN_HITS = int(os.getenv("LLAMORA_POOR_MATCH_MIN_HITS", 3))
