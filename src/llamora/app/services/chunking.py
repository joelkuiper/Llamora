from __future__ import annotations


def _words_with_lengths(text: str) -> list[tuple[str, int]]:
    words = []
    for raw in text.split():
        word = raw.strip()
        if not word:
            continue
        words.append((word, len(word)))
    return words


def _take_overlap(
    words: list[tuple[str, int]], overlap_chars: int
) -> list[tuple[str, int]]:
    if overlap_chars <= 0 or not words:
        return []
    total = 0
    overlap: list[tuple[str, int]] = []
    for word, length in reversed(words):
        overlap.append((word, length))
        total += length + 1
        if total >= overlap_chars:
            break
    return list(reversed(overlap))


def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if not text:
        return []
    if max_chars <= 0:
        return [text.strip()]

    words = _words_with_lengths(text)
    if not words:
        return []

    chunks: list[str] = []
    current: list[tuple[str, int]] = []
    current_len = 0

    for word, length in words:
        extra = length + (1 if current else 0)
        if current and current_len + extra > max_chars:
            chunk = " ".join(w for w, _ in current).strip()
            if chunk:
                chunks.append(chunk)
            overlap = _take_overlap(current, overlap_chars)
            current = overlap[:]
            current_len = sum(length for _, length in current) + max(
                0, len(current) - 1
            )
        if not current:
            current = [(word, length)]
            current_len = length
        else:
            current.append((word, length))
            current_len += extra

    if current:
        chunk = " ".join(w for w, _ in current).strip()
        if chunk:
            chunks.append(chunk)

    return chunks
