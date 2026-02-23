import asyncio
import logging
import os
import threading

import numpy as np
from fastembed import TextEmbedding

from llamora.settings import settings

logger = logging.getLogger(__name__)

_embed_semaphore = asyncio.Semaphore(
    settings.EMBEDDING.concurrency or os.cpu_count() or 1
)
_SEMAPHORE_TIMEOUT = 30.0

_model_lock = threading.Lock()
_cached_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    """Return the shared embedding model, creating it on first call.

    Unlike ``@lru_cache`` this does not cache exceptions — a transient
    download or initialisation failure will be retried on the next call.
    """
    global _cached_model
    if _cached_model is not None:
        return _cached_model
    with _model_lock:
        if _cached_model is not None:
            return _cached_model
        model = TextEmbedding(model_name=settings.EMBEDDING.model)
        _cached_model = model
        return model


def embed_texts(texts: list[str]) -> np.ndarray:
    # normalize=True returns L2-normalized vectors (safe for cosine/HNSW)
    vecs = list(_get_model().embed(texts, normalize=True))
    return np.asarray(vecs, dtype=np.float32)


async def async_embed_texts(texts: list[str]) -> np.ndarray:
    """Run :func:`embed_texts` without blocking the event loop."""

    try:
        await asyncio.wait_for(_embed_semaphore.acquire(), timeout=_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"Embedding semaphore not acquired within {_SEMAPHORE_TIMEOUT}s"
        )
    try:
        return await asyncio.to_thread(embed_texts, texts)
    finally:
        _embed_semaphore.release()
