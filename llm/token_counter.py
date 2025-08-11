from __future__ import annotations

from llama_cpp import Llama


class TokenCounter:
    """Utility for counting tokens using llama.cpp's vocab-only tokenizer."""

    def __init__(self, model_path: str):
        # vocab_only=True loads only tokenizer without model weights
        self._tokenizer = Llama(model_path=model_path, vocab_only=True)

    def count(self, text: str) -> int:
        """Return number of tokens in *text*."""
        tokens = self._tokenizer.tokenize(text.encode("utf-8"), add_bos=False)
        return len(tokens)
