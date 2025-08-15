import numpy as np
import os
from functools import lru_cache
from sentence_transformers import SentenceTransformer

_MODEL_NAME = os.getenv(
    "LLAMORA_EMBED_MODEL", "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
)


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(_MODEL_NAME)


def embed_texts(texts: list[str]) -> np.ndarray:
    model = _get_model()
    # Normalize embeddings for cosine similarity search
    vecs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return vecs.astype(np.float32)
