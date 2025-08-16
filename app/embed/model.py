import numpy as np
import os
from functools import lru_cache

from fastembed import TextEmbedding

_MODEL = os.getenv("LLAMORA_EMBED_MODEL", "BAAI/bge-small-en-v1.5")  # 384-dim


@lru_cache(maxsize=1)
def _get_model() -> TextEmbedding:
    # downloads once to ~/.cache; CPU-only and fast
    return TextEmbedding(model_name=_MODEL)


def embed_texts(texts: list[str]) -> np.ndarray:
    # normalize=True returns L2-normalized vectors (safe for cosine/HNSW)
    vecs = list(_get_model().embed(texts, normalize=True))
    return np.asarray(vecs, dtype=np.float32)
