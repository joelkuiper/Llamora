import asyncio
import os
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

from llamora.settings import settings

_embed_semaphore = asyncio.Semaphore(
    settings.EMBEDDING.concurrency or os.cpu_count() or 1
)


@lru_cache(maxsize=1)
def _get_model() -> TextEmbedding:
    # downloads once to ~/.cache; CPU-only and fast
    return TextEmbedding(model_name=settings.EMBEDDING.model)


def embed_texts(texts: list[str]) -> np.ndarray:
    # normalize=True returns L2-normalized vectors (safe for cosine/HNSW)
    vecs = list(_get_model().embed(texts, normalize=True))
    return np.asarray(vecs, dtype=np.float32)


async def async_embed_texts(texts: list[str]) -> np.ndarray:
    """Run :func:`embed_texts` without blocking the event loop."""

    async with _embed_semaphore:
        return await asyncio.to_thread(embed_texts, texts)
