import numpy as np
from functools import lru_cache

from fastembed import TextEmbedding
from quart import current_app
import config


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
