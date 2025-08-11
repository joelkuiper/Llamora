"""Application configuration constants and settings.

This module centralizes both general application settings and parameters for the local LLM backend.  The LLM settings are exposed via dictionaries so additional
`llama_cpp.Llama` keyword arguments can be provided without touching the rest of the codebase.
"""

import os
from util import str_to_bool

MAX_USERNAME_LENGTH = 30
MAX_PASSWORD_LENGTH = 128
MIN_PASSWORD_LENGTH = 8
MAX_MESSAGE_LENGTH = 1000
MAX_RESPONSE_TOKENS = 1024
MAX_SESSION_NAME_LENGTH = 100
APP_NAME = "Llamora"


def _env_bool(name: str, default: str = "False") -> bool:
    """Return the environment variable as a boolean."""

    return str_to_bool(os.getenv(name, default))


# LLM / llama.cpp configuration

# Arguments passed directly to ``langchain_community.llms.LlamaCpp``.
# Any additional llama.cpp parameters can be added here and will be forwarded to the underlying model constructor.
LLAMA_CPP_KWARGS: dict = {
    "n_ctx": 1024 * 9,
    "n_gpu_layers": -1,
    "temperature": 0.8,
    "max_tokens": MAX_RESPONSE_TOKENS,
    "streaming": True,
}

LLM_ENGINE_CONFIG: dict = {
    "model_path": os.environ["CHAT_MODEL_GGUF"],
    "max_workers": int(os.getenv("CHAT_LLM_WORKERS", "1")),
    "verbose": _env_bool("QUART_DEBUG"),
    "llama_cpp_kwargs": LLAMA_CPP_KWARGS,
}
