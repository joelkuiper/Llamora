import asyncio
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding
from quart import current_app

from llamora import config

_embed_semaphore = asyncio.Semaphore(max(config.EMBED_CONCURRENCY, 1))


@lru_cache(maxsize=1)
def _get_model() -> TextEmbedding:
    # downloads once to ~/.cache; CPU-only and fast
    try:
        model_name = current_app.config.get("EMBED_MODEL", config.EMBED_MODEL)
    except RuntimeError:
        model_name = config.EMBED_MODEL
    return TextEmbedding(model_name=model_name)


def embed_texts(texts: list[str]) -> np.ndarray:
    # normalize=True returns L2-normalized vectors (safe for cosine/HNSW)
    vecs = list(_get_model().embed(texts, normalize=True))
    return np.asarray(vecs, dtype=np.float32)


async def async_embed_texts(texts: list[str]) -> np.ndarray:
    """Run :func:`embed_texts` without blocking the event loop."""

    async with _embed_semaphore:
        return await asyncio.to_thread(embed_texts, texts)
