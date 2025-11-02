"""Utilities for parsing streamed chat metadata."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

try:
    import orjson
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    orjson = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


@dataclass
class ChatMetaParser:
    """Extracts assistant-visible text and metadata fragments from chunks."""

    sentinel_start: str = "<meta>"
    sentinel_end: str = "</meta>"
    tail_limit: int = 256

    def __post_init__(self) -> None:
        self._found_start = False
        self._meta_complete = False
        self._meta_buffer: str = ""
        self._tail: str = ""
        self._raw_buffer: str = ""
        self._orphan_meta = False

    def feed(self, chunk: str) -> str:
        """Process a chunk and return the user-visible portion."""

        self._raw_buffer += chunk
        data = self._tail + chunk
        self._tail = ""
        visible = ""

        if not self._found_start:
            idx = data.find(self.sentinel_start)
            end_idx = data.find(self.sentinel_end)
            if end_idx != -1 and (idx == -1 or end_idx < idx):
                visible = data[:end_idx]
                remainder = data[end_idx + len(self.sentinel_end) :]
                candidate = extract_json_candidate(remainder)
                trailing = ""
                if candidate:
                    candidate_idx = remainder.find(candidate)
                    meta_fragment = remainder[: candidate_idx + len(candidate)]
                    trailing = remainder[candidate_idx + len(candidate) :]
                else:
                    meta_fragment = remainder
                self._found_start = True
                self._meta_buffer += meta_fragment
                if candidate:
                    self._meta_complete = True
                if trailing:
                    visible += trailing
                return visible
            if idx != -1:
                visible = data[:idx]
                data = data[idx + len(self.sentinel_start) :]
                self._found_start = True
                self._meta_buffer += data
            else:
                visible, self._tail = self._split_visible_tail(data)
                self._maybe_claim_orphan_meta()
                return visible
        else:
            self._meta_buffer += data

        if not self._meta_complete:
            end_idx = self._meta_buffer.find(self.sentinel_end)
            if end_idx != -1:
                trailing = self._meta_buffer[end_idx + len(self.sentinel_end) :]
                if trailing.strip():
                    logger.debug(
                        "Unexpected trailing content after %s: %r",
                        self.sentinel_end,
                        trailing,
                    )
                self._meta_buffer = self._meta_buffer[:end_idx]
                self._meta_complete = True

        return visible

    def flush_visible_tail(self) -> str:
        """Return any buffered visible text when no meta was found."""

        if self._found_start or self._orphan_meta:
            return ""
        remainder = self._tail
        self._tail = ""
        return remainder

    def _split_visible_tail(self, data: str) -> tuple[str, str]:
        if not data:
            return "", ""
        minimal_keep = max(len(self.sentinel_end), len(self.sentinel_start) - 1, 0)
        stripped = data.rstrip("\r\n")
        trailing_newlines = data[len(stripped) :]
        last_newline = stripped.rfind("\n")
        if last_newline != -1:
            tail = stripped[last_newline + 1 :] + trailing_newlines
        else:
            tail = stripped + trailing_newlines
        original_tail = tail
        candidate = extract_json_candidate(tail)
        if candidate:
            candidate_idx = tail.rfind(candidate)
            if candidate_idx != -1:
                prefix = tail[:candidate_idx]
                kept = tail[candidate_idx:]
                if len(kept) > self.tail_limit:
                    kept = kept[-self.tail_limit :]
                base_visible = data[: len(data) - len(original_tail)]
                return base_visible + prefix, kept
        if "{" in tail:
            brace_idx = tail.find("{")
            prefix = tail[:brace_idx]
            kept = tail[brace_idx:]
            if len(kept) > self.tail_limit:
                kept = kept[-self.tail_limit :]
            base_visible = data[: len(data) - len(original_tail)]
            return base_visible + prefix, kept
        kept_tail = tail[-minimal_keep:]
        if not kept_tail and minimal_keep:
            kept_tail = data[-minimal_keep:]
        if len(data) <= len(kept_tail):
            return "", data
        return data[: len(data) - len(kept_tail)], kept_tail

    def _maybe_claim_orphan_meta(self) -> None:
        if self._found_start or self._orphan_meta:
            return
        candidate = extract_json_candidate(self._tail)
        if not candidate:
            return
        meta = parse_meta_json(candidate)
        if not isinstance(meta, dict):
            return
        if not {"emoji", "keywords"}.issubset(meta.keys()):
            return
        self._found_start = True
        self._meta_buffer = candidate
        self._meta_complete = True
        self._orphan_meta = True
        self._tail = ""

    @property
    def meta_complete(self) -> bool:
        return self._meta_complete

    @property
    def meta_payload(self) -> str:
        return self._meta_buffer.strip()

    @property
    def raw_buffer(self) -> str:
        return self._raw_buffer


def recover_meta_payload(parser: ChatMetaParser) -> str | None:
    """Recover the last plausible meta payload from the raw buffer."""

    raw = parser.raw_buffer
    if not raw:
        return None
    start_idx = raw.rfind(parser.sentinel_start)
    if start_idx != -1:
        raw_segment = raw[start_idx + len(parser.sentinel_start) :]
    else:
        raw_segment = raw
    end_idx = raw_segment.rfind(parser.sentinel_end)
    if end_idx != -1:
        raw_segment = raw_segment[:end_idx]
    raw_segment = raw_segment.strip()
    if not raw_segment:
        return None
    candidate = find_last_json_object(raw_segment)
    if candidate:
        return candidate.strip()
    first_brace = raw_segment.find("{")
    if first_brace == -1:
        return None
    trailing = raw_segment[first_brace:]
    candidate = find_last_json_object(trailing)
    if candidate:
        return candidate.strip()
    return trailing.strip() or None


def parse_meta_json(payload: str | None) -> dict[str, Any] | None:
    """Parse a JSON dictionary from the provided payload."""

    candidate = extract_json_candidate(payload)
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        if orjson is None:  # pragma: no cover - optional dependency
            return None
        try:
            obj = orjson.loads(candidate)
        except Exception:
            return None
    if isinstance(obj, dict):
        return obj
    return None


def extract_json_candidate(payload: str | None) -> str | None:
    if not payload:
        return None
    text = payload.strip()
    if not text:
        return None
    first_brace = text.find("{")
    if first_brace == -1:
        return None
    snippet = text[first_brace:]
    candidate = find_last_json_object(snippet)
    if candidate:
        return candidate.strip()
    decoder = json.JSONDecoder()
    try:
        _, end = decoder.raw_decode(snippet)
    except json.JSONDecodeError:
        last_brace = snippet.rfind("}")
        if last_brace == -1:
            return None
        narrowed = snippet[: last_brace + 1]
        candidate = find_last_json_object(narrowed)
        if candidate:
            return candidate.strip()
        return None
    else:
        return snippet[:end].strip()


def find_last_json_object(text: str) -> str | None:
    in_string = False
    escape_next = False
    depth = 0
    end = None
    for idx in range(len(text) - 1, -1, -1):
        ch = text[idx]
        if in_string:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "}":
            if depth == 0:
                end = idx + 1
            depth += 1
        elif ch == "{":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and end is not None:
                return text[idx:end]
    return None


def build_meta(
    parser: ChatMetaParser, *, meta_extra: dict | None = None, error: bool = False
) -> dict:
    """Assemble a metadata dictionary from parser output and extras."""

    candidates: list[str | None] = [parser.meta_payload]
    recovered = recover_meta_payload(parser)
    if recovered and recovered not in candidates:
        candidates.append(recovered)

    meta: dict[str, Any] | None = None
    for attempt in candidates:
        meta = parse_meta_json(attempt)
        if meta is not None:
            break
    if meta is None:
        meta = {}
    if error:
        meta["error"] = True
    if meta_extra:
        meta.update(meta_extra)
    return meta


__all__ = [
    "ChatMetaParser",
    "build_meta",
    "extract_json_candidate",
    "find_last_json_object",
    "parse_meta_json",
    "recover_meta_payload",
]
