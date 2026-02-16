import asyncio
import logging
import time
from typing import Dict, Iterable, Optional, cast

import hnswlib
import numpy as np

from llamora.app.embed.model import async_embed_texts
from llamora.app.services.chunking import chunk_text
from llamora.app.services.crypto import EncryptionContext
from llamora.settings import settings


logger = logging.getLogger(__name__)


def _vector_id(entry_id: str, chunk_index: int) -> str:
    return f"{entry_id}::c{chunk_index}"


def _entry_id_from_vector_id(vector_id: str) -> str:
    if "::c" in vector_id:
        return vector_id.split("::c", 1)[0]
    return vector_id


class EntryIndex:
    """In-memory ANN index for a single user's entries."""

    def __init__(
        self,
        dim: int,
        max_elements: int = 100000,
        *,
        allow_growth: bool = False,
    ):
        self.index = hnswlib.Index(space="cosine", dim=dim)
        self.allow_growth = bool(allow_growth)
        self.index.init_index(
            max_elements=max_elements,
            ef_construction=200,
            M=32,
            allow_replace_deleted=not self.allow_growth,
        )
        self.index.set_ef(64)
        self.id_to_idx: Dict[str, int] = {}
        self.idx_to_id: Dict[int, str] = {}
        self.entry_to_ids: Dict[str, set[str]] = {}
        self._free_idxs: list[int] = []
        self._free_set: set[int] = set()
        self.next_idx = 0
        self.last_used = time.monotonic()
        self.dim = dim
        self.max_elements = max_elements

    def estimated_memory_bytes(self) -> int:
        vector_count = len(self.id_to_idx)
        base_vectors = vector_count * self.dim * np.dtype(np.float32).itemsize
        graph_overhead = vector_count * 128
        id_bytes = sum(len(vector_id) for vector_id in self.id_to_idx)
        reverse_id_bytes = sum(len(vector_id) for vector_id in self.idx_to_id.values())
        mapping_overhead = (len(self.id_to_idx) + len(self.idx_to_id)) * 72
        entry_map_bytes = 0
        for entry_id, vector_ids in self.entry_to_ids.items():
            entry_map_bytes += len(entry_id) + 64
            entry_map_bytes += sum(len(vector_id) + 16 for vector_id in vector_ids)
        free_list_bytes = len(self._free_idxs) * 8 + len(self._free_set) * 16
        return (
            base_vectors
            + graph_overhead
            + id_bytes
            + reverse_id_bytes
            + mapping_overhead
            + entry_map_bytes
            + free_list_bytes
        )

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def contains_entry(self, entry_id: str) -> bool:
        """Return True if any vectors for entry_id are indexed."""
        return entry_id in self.entry_to_ids

    def add_batch(self, ids: list[str], vecs: np.ndarray) -> None:
        if not ids:
            return
        vecs = np.asarray(vecs, dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        if vecs.shape[0] != len(ids):
            raise ValueError("ids and vecs length mismatch")

        pairs = [(mid, vec) for mid, vec in zip(ids, vecs) if mid not in self.id_to_idx]
        if not pairs:
            return

        id_list, vec_list = zip(*pairs)
        ids = list(id_list)
        vecs = np.asarray(vec_list, dtype=np.float32)

        self.touch()

        if self.allow_growth:
            required = self.next_idx + len(ids)
            current_capacity = self.max_elements
            if required > current_capacity:
                new_capacity = max(current_capacity * 2, required)
                logger.warning(
                    "Resizing entry index from %d to %d to accommodate %d new items",
                    current_capacity,
                    new_capacity,
                    len(ids),
                )
                self.index.resize_index(new_capacity)
                self.max_elements = new_capacity

            idxs = list(range(self.next_idx, self.next_idx + len(ids)))
            logger.debug(
                "Adding %d vectors starting at index %d", len(ids), self.next_idx
            )
            self.index.add_items(vecs, idxs)
            for entry_id, idx in zip(ids, idxs):
                self.id_to_idx[entry_id] = int(idx)
                self.idx_to_id[int(idx)] = entry_id
                parent_id = _entry_id_from_vector_id(entry_id)
                self.entry_to_ids.setdefault(parent_id, set()).add(entry_id)
            self.next_idx += len(ids)
            return

        if self.max_elements <= 0:
            logger.warning("Entry index capacity is zero; skipping %d items", len(ids))
            return

        incoming = len(ids)
        if incoming > self.max_elements:
            ids = ids[: self.max_elements]
            vecs = vecs[: self.max_elements]
            incoming = len(ids)

        active = len(self.id_to_idx)
        room = self.max_elements - active
        if incoming > room:
            to_evict = incoming - room
            self._evict_oldest(to_evict)
            active = len(self.id_to_idx)
            room = self.max_elements - active
            if room <= 0:
                logger.warning(
                    "Entry index at capacity after eviction; dropping %d items",
                    incoming,
                )
                return
            if incoming > room:
                ids = ids[:room]
                vecs = vecs[:room]
                incoming = len(ids)

        idxs: list[int] = []
        while self._free_idxs and len(idxs) < incoming:
            idx = self._free_idxs.pop()
            self._free_set.discard(idx)
            idxs.append(idx)

        if len(idxs) < incoming:
            needed = incoming - len(idxs)
            start = self.next_idx
            end = min(self.next_idx + needed, self.max_elements)
            idxs.extend(range(start, end))
            self.next_idx = end
            if len(idxs) < incoming:
                ids = ids[: len(idxs)]
                vecs = vecs[: len(idxs)]
                incoming = len(ids)

        logger.debug("Adding %d vectors with cap=%d", incoming, self.max_elements)
        self.index.add_items(vecs, idxs, replace_deleted=True)
        for entry_id, idx in zip(ids, idxs):
            self.id_to_idx[entry_id] = int(idx)
            self.idx_to_id[int(idx)] = entry_id
            parent_id = _entry_id_from_vector_id(entry_id)
            self.entry_to_ids.setdefault(parent_id, set()).add(entry_id)

    def search(self, query_vec: np.ndarray, k: int) -> tuple[list[str], np.ndarray]:
        self.touch()
        query_vec = np.asarray(query_vec, dtype=np.float32)
        if query_vec.ndim == 1:
            query_vec = query_vec.reshape(1, -1)
        count = self.index.get_current_count()
        available = len(self.idx_to_id)
        if count == 0 or available == 0:
            logger.debug("Search invoked on empty index")
            return [], np.array([], dtype=np.float32)
        k = min(k, count, available)
        if k <= 0:
            logger.debug("Search invoked with no available vectors")
            return [], np.array([], dtype=np.float32)
        ef = max(k, 64)
        self.index.set_ef(ef)
        logger.debug("Searching %d vectors with k=%d ef=%d", count, k, ef)
        labels_arr, dists = cast(
            tuple[np.ndarray, np.ndarray], self.index.knn_query(query_vec, k=k)
        )
        ids: list[str] = []
        for label in labels_arr[0]:
            idx_label = int(label)
            vector_id = self.idx_to_id.get(idx_label)
            if vector_id is not None:
                ids.append(vector_id)
        return ids, dists[0][: len(ids)]

    def remove_entries(self, entry_ids: Iterable[str]) -> None:
        removed = False
        for entry_id in entry_ids:
            vector_ids = self.entry_to_ids.pop(entry_id, set())
            for vector_id in vector_ids:
                removed |= self._remove_vector_id(vector_id)
        if removed:
            self.touch()

    def _remove_vector_id(self, vector_id: str) -> bool:
        idx = self.id_to_idx.pop(vector_id, None)
        if idx is None:
            return False
        self.idx_to_id.pop(idx, None)
        parent_id = _entry_id_from_vector_id(vector_id)
        vector_set = self.entry_to_ids.get(parent_id)
        if vector_set is not None:
            vector_set.discard(vector_id)
            if not vector_set:
                self.entry_to_ids.pop(parent_id, None)
        if hasattr(self.index, "mark_deleted"):
            self.index.mark_deleted(idx)
        if idx not in self._free_set:
            self._free_idxs.append(idx)
            self._free_set.add(idx)
        return True

    def _evict_oldest(self, count: int) -> None:
        remaining = max(int(count), 0)
        if remaining <= 0:
            return
        for vector_id in sorted(self.id_to_idx.keys())[:remaining]:
            self._remove_vector_id(vector_id)


class EntryIndexStore:
    """Manages per-user ANN indexes, persistence and maintenance."""

    def __init__(
        self,
        db,
        ttl: int = 600,
        warm_limit: int = 1000,
        maintenance_interval: float = 60.0,
        max_elements: int = 100_000,
        allow_growth: bool = False,
        chunk_max_chars: int | None = None,
        chunk_overlap_chars: int | None = None,
        global_memory_budget_bytes: int | None = None,
        service_pulse=None,
    ):
        self.db = db
        self.ttl = ttl
        self.warm_limit = warm_limit
        self.maintenance_interval = maintenance_interval
        self.max_elements = max_elements
        self.allow_growth = bool(allow_growth)
        embedding_cfg = getattr(settings, "EMBEDDING", {})
        chunk_cfg = embedding_cfg.get("chunking", {})
        index_cfg = embedding_cfg.get("index", {})
        vector_cfg = embedding_cfg.get("vectors", {})
        self.chunk_max_chars = int(
            chunk_max_chars
            if chunk_max_chars is not None
            else chunk_cfg.get("max_chars", 1200)
        )
        self.chunk_overlap_chars = int(
            chunk_overlap_chars
            if chunk_overlap_chars is not None
            else chunk_cfg.get("overlap_chars", 200)
        )
        self.embed_batch_size = int(index_cfg.get("embed_batch_size", 128))
        self.warm_entry_batch = int(index_cfg.get("warm_entry_batch", 200))
        dtype = str(vector_cfg.get("dtype", "float32")).lower()
        if dtype not in {"float32", "float16"}:
            logger.warning(
                "Unsupported vector dtype '%s'; defaulting to float32", dtype
            )
            dtype = "float32"
        self.vector_dtype = dtype
        self._vector_np_dtype = np.float16 if dtype == "float16" else np.float32
        self.indexes: Dict[str, EntryIndex] = {}
        self.cursors: Dict[str, Optional[str]] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self._next_maintenance = time.monotonic() + maintenance_interval
        self._default_dim: Optional[int] = None
        self._default_dim_lock = asyncio.Lock()
        self._warm_tasks: Dict[str, asyncio.Task] = {}
        budget = (
            global_memory_budget_bytes
            if global_memory_budget_bytes is not None
            else embedding_cfg.get("global_memory_budget_bytes", 256 * 1024 * 1024)
        )
        self._global_memory_budget_bytes = max(int(budget), 0)
        self._service_pulse = service_pulse

    def _estimate_total_memory_bytes(self) -> int:
        return sum(index.estimated_memory_bytes() for index in self.indexes.values())

    def _emit_budget_pressure(self, *, evicted: int, total_bytes: int) -> None:
        if self._service_pulse is None:
            return
        budget = self._global_memory_budget_bytes
        pressure = (total_bytes / budget) if budget > 0 else 0.0
        payload = {
            "budget_bytes": budget,
            "total_bytes": total_bytes,
            "pressure": pressure,
            "evicted_indexes": evicted,
            "active_indexes": len(self.indexes),
        }
        try:
            self._service_pulse.emit("search.entry_index_budget", payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to emit entry index budget pulse")

    def _enforce_global_budget(self) -> None:
        budget = self._global_memory_budget_bytes
        if budget <= 0 or not self.indexes:
            return
        total_bytes = self._estimate_total_memory_bytes()
        evicted = 0
        if total_bytes > budget:
            ordered = sorted(self.indexes.items(), key=lambda item: item[1].last_used)
            for user_id, index in ordered:
                if total_bytes <= budget:
                    break
                reclaimed = index.estimated_memory_bytes()
                self.indexes.pop(user_id, None)
                self.cursors.pop(user_id, None)
                self.locks.pop(user_id, None)
                self._warm_tasks.pop(user_id, None)
                total_bytes = max(total_bytes - reclaimed, 0)
                evicted += 1
        self._emit_budget_pressure(evicted=evicted, total_bytes=total_bytes)

    def _to_storage_vecs(self, vecs: np.ndarray) -> np.ndarray:
        if self._vector_np_dtype == np.float32:
            return vecs
        return np.asarray(vecs, dtype=self._vector_np_dtype)

    def is_warming(self, user_id: str) -> bool:
        task = self._warm_tasks.get(user_id)
        return bool(task and not task.done())

    def _chunk_entry(self, text: str) -> list[str]:
        chunks = chunk_text(text, self.chunk_max_chars, self.chunk_overlap_chars)
        if chunks:
            return chunks
        cleaned = text.strip()
        return [cleaned] if cleaned else []

    def _get_lock(self, user_id: str) -> asyncio.Lock:
        return self.locks.setdefault(user_id, asyncio.Lock())

    async def _get_default_dim(self) -> int:
        if self._default_dim is not None:
            return self._default_dim

        async with self._default_dim_lock:
            if self._default_dim is None:
                self._default_dim = (await async_embed_texts([""])).shape[1]
        if self._default_dim is None:  # pragma: no cover - defensive
            raise RuntimeError("Failed to determine embedding dimension")
        return self._default_dim

    async def _embed_and_store(
        self,
        ctx: EncryptionContext,
        entries: Iterable[dict],
        idx: Optional[EntryIndex],
    ) -> EntryIndex:
        """Embed entries, add to index and persist vectors."""

        entry_list = list(entries)
        if not entry_list:
            if idx is not None:
                return idx
            dim = await self._get_default_dim()
            fresh = EntryIndex(dim, self.max_elements, allow_growth=self.allow_growth)
            self.indexes[ctx.user_id] = fresh
            return fresh

        vector_texts: list[str] = []
        vector_ids: list[str] = []
        vector_entries: list[str] = []
        vector_chunks: list[int] = []
        for entry in entry_list:
            entry_id = entry["id"]
            chunks = self._chunk_entry(entry.get("text", ""))
            for chunk_index, chunk in enumerate(chunks):
                vector_texts.append(chunk)
                vector_entries.append(entry_id)
                vector_chunks.append(chunk_index)
                vector_ids.append(_vector_id(entry_id, chunk_index))

        if not vector_texts:
            return idx or EntryIndex(
                await self._get_default_dim(),
                self.max_elements,
                allow_growth=self.allow_growth,
            )

        batch_size = max(self.embed_batch_size, 1)
        for start in range(0, len(vector_texts), batch_size):
            end = start + batch_size
            batch_texts = vector_texts[start:end]
            batch_ids = vector_ids[start:end]
            batch_entries = vector_entries[start:end]
            batch_chunks = vector_chunks[start:end]
            vecs = await async_embed_texts(batch_texts)
            if idx is None:
                dim = vecs.shape[1]
                idx = EntryIndex(dim, self.max_elements, allow_growth=self.allow_growth)
            idx.add_batch(batch_ids, vecs)
            store_vecs = self._to_storage_vecs(vecs)
            await self.db.vectors.store_vectors_batch(
                [
                    (vector_id, entry_id, chunk_index, vec)
                    for vector_id, entry_id, chunk_index, vec in zip(
                        batch_ids, batch_entries, batch_chunks, store_vecs
                    )
                ],
                ctx,
                self.vector_dtype,
            )
            await asyncio.sleep(0)
        if entry_list:
            cursor = entry_list[-1]["id"]
            existing = self.cursors.get(ctx.user_id)
            if existing is None or cursor < existing:
                self.cursors[ctx.user_id] = cursor
        # idx is guaranteed non-None here: we only reach this point if vector_texts
        # was non-empty (otherwise we returned at line 336), and the first loop
        # iteration assigns idx if it was None (line 352).
        assert idx is not None
        self.indexes[ctx.user_id] = idx
        return idx

    async def ensure_index(
        self, user_id: str, dek: bytes, ctx: EncryptionContext | None = None
    ) -> EntryIndex:
        logger.debug("Ensuring index for user %s", user_id)
        lock = self._get_lock(user_id)
        async with lock:
            idx = self.indexes.get(user_id)
            if idx:
                logger.debug("Cache hit for user %s", user_id)
                idx.touch()
                return idx

            rows = await self.db.vectors.get_latest_vectors(
                user_id, self.warm_limit, dek
            )
            if rows:
                logger.debug(
                    "Warming index for user %s with %d vectors", user_id, len(rows)
                )
                dim = rows[0]["vec"].shape[0]
                idx = EntryIndex(dim, self.max_elements, allow_growth=self.allow_growth)
                ids = [r["id"] for r in rows]
                vecs = np.array([r["vec"] for r in rows], dtype=np.float32)
                if vecs.ndim == 1:
                    vecs = vecs.reshape(1, -1)
                idx.add_batch(ids, vecs)
                cursor = rows[-1]["entry_id"]
                self.cursors[user_id] = cursor
                self.indexes[user_id] = idx

                entries = await self.db.entries.get_latest_entries(
                    user_id, self.warm_limit, dek
                )
                missing = [
                    entry for entry in entries if not idx.contains_entry(entry["id"])
                ]
                if missing:
                    logger.debug(
                        "Queueing %d entries missing vectors for user %s",
                        len(missing),
                        user_id,
                    )
                    if ctx is None:
                        logger.warning(
                            "Skipping vector warm-up for user %s; missing encryption context",
                            user_id,
                        )
                    else:
                        self._schedule_warm(ctx, missing)
                return idx

            entries = await self.db.entries.get_latest_entries(
                user_id, self.warm_limit, dek
            )
            if entries:
                logger.debug(
                    "Embedding and indexing %d entries for user %s",
                    len(entries),
                    user_id,
                )
                dim = await self._get_default_dim()
                idx = EntryIndex(dim, self.max_elements, allow_growth=self.allow_growth)
                self.indexes[user_id] = idx
                cursor = entries[-1]["id"]
                self.cursors[user_id] = cursor
                if ctx is None:
                    logger.warning(
                        "Skipping vector warm-up for user %s; missing encryption context",
                        user_id,
                    )
                else:
                    self._schedule_warm(ctx, entries)
                return idx

            logger.debug("No existing data for user %s, creating empty index", user_id)
            dim = await self._get_default_dim()
            idx = EntryIndex(dim, self.max_elements, allow_growth=self.allow_growth)
            latest = await self.db.entries.get_user_latest_entry_id(user_id)
            self.cursors[user_id] = latest
            self.indexes[user_id] = idx
            return idx

    def _schedule_warm(self, ctx: EncryptionContext, entries: list[dict]) -> None:
        user_id = ctx.user_id
        existing = self._warm_tasks.get(user_id)
        if existing and not existing.done():
            return

        async def worker() -> None:
            try:
                batch_size = max(self.warm_entry_batch, 1)
                for start in range(0, len(entries), batch_size):
                    batch = entries[start : start + batch_size]
                    if not batch:
                        break
                    lock = self._get_lock(user_id)
                    async with lock:
                        idx = self.indexes.get(user_id)
                        await self._embed_and_store(ctx, batch, idx)
                    await asyncio.sleep(0)
            except Exception:
                logger.exception("Warm-up failed for user %s", user_id)
            finally:
                task = self._warm_tasks.get(user_id)
                if task is not None and task.done():
                    self._warm_tasks.pop(user_id, None)

        self._warm_tasks[user_id] = asyncio.create_task(worker())

    async def expand_older(self, ctx: EncryptionContext, batch: int) -> int:
        user_id = ctx.user_id
        dek = ctx.dek
        await self.ensure_index(user_id, dek, ctx)
        lock = self._get_lock(user_id)
        async with lock:
            cursor = self.cursors.get(user_id)
            if not cursor:
                logger.debug("No cursor for user %s", user_id)
                return 0

            idx = self.indexes.get(user_id)
            if idx is None:
                logger.debug("Index missing for user %s", user_id)
                return 0

            added = 0
            new_cursor = cursor

            rows = await self.db.vectors.get_vectors_older_than(
                user_id, cursor, batch, dek
            )
            if rows:
                logger.debug(
                    "Loaded %d stored vectors older than %s for user %s",
                    len(rows),
                    cursor,
                    user_id,
                )
                ids = [r["id"] for r in rows]
                vecs = np.array([r["vec"] for r in rows], dtype=np.float32)
                if vecs.ndim == 1:
                    vecs = vecs.reshape(1, -1)
                idx.add_batch(ids, vecs)
                added += len(ids)
                new_cursor = rows[-1]["entry_id"]

            entries = await self.db.entries.get_entries_older_than(
                user_id, cursor, batch, dek
            )
            missing = [
                entry for entry in entries if not idx.contains_entry(entry["id"])
            ]
            if missing:
                logger.debug(
                    "Embedding %d entries older than %s for user %s",
                    len(missing),
                    cursor,
                    user_id,
                )
                await self._embed_and_store(ctx, missing, idx)
                added += len(missing)
                new_cursor = missing[-1]["id"]

            if added == 0:
                logger.debug("No older entries for user %s", user_id)
                return 0

            existing = self.cursors.get(user_id)
            if existing is None or new_cursor < existing:
                self.cursors[user_id] = new_cursor
            return added

    async def hydrate_entries(
        self, user_id: str, entry_ids: list[str], dek: bytes
    ) -> list[dict]:
        if not entry_ids:
            return []
        rows = await self.db.entries.get_entries_by_ids(user_id, entry_ids, dek)
        return rows

    async def index_entry(
        self, ctx: EncryptionContext, entry_id: str, content: str
    ) -> None:
        await self.bulk_index([(ctx, entry_id, content)])

    async def bulk_index(
        self, entries: Iterable[tuple[EncryptionContext, str, str]]
    ) -> None:
        items = list(entries)
        if not items:
            return

        pending: list[tuple[EncryptionContext, str, str, int, str]] = []
        for ctx, entry_id, content in items:
            chunks = self._chunk_entry(content)
            for chunk_index, chunk_part in enumerate(chunks):
                vector_id = _vector_id(entry_id, chunk_index)
                pending.append((ctx, entry_id, vector_id, chunk_index, chunk_part))

        if not pending:
            return

        texts = [chunk_text for _, _, _, _, chunk_text in pending]
        vecs = await async_embed_texts(texts)
        if vecs.shape[0] != len(pending):  # pragma: no cover - defensive
            raise ValueError("Embedding count does not match input items")

        per_user_vectors: Dict[
            str, list[tuple[str, str, int, np.ndarray, EncryptionContext]]
        ] = {}
        for (ctx, entry_id, vector_id, chunk_index, _), vec in zip(pending, vecs):
            per_user_vectors.setdefault(ctx.user_id, []).append(
                (vector_id, entry_id, chunk_index, vec, ctx)
            )

        logger.debug(
            "Bulk indexing %d entries across %d users",
            len(items),
            len(per_user_vectors),
        )

        for user_id, user_entries in per_user_vectors.items():
            ctx = user_entries[0][4]
            idx = await self.ensure_index(user_id, ctx.dek, ctx)
            lock = self._get_lock(user_id)
            ids = [vector_id for vector_id, _, _, _, _ in user_entries]
            vec_arr = np.asarray(
                [vec for _, _, _, vec, _ in user_entries], dtype=np.float32
            )
            async with lock:
                store_vecs = self._to_storage_vecs(vec_arr)
                await self.db.vectors.store_vectors_batch(
                    [
                        (vector_id, entry_id, chunk_index, vec)
                        for vector_id, entry_id, chunk_index, vec in zip(
                            ids,
                            [entry_id for _, entry_id, _, _, _ in user_entries],
                            [chunk_index for _, _, chunk_index, _, _ in user_entries],
                            store_vecs,
                        )
                    ],
                    ctx,
                    self.vector_dtype,
                )
                idx.add_batch(ids, vec_arr)
                for entry_id in {entry_id for _, entry_id, _, _, _ in user_entries}:
                    current = self.cursors.get(user_id)
                    if current is None or entry_id < current:
                        self.cursors[user_id] = entry_id
                self.indexes[user_id] = idx

    def _evict_idle(self) -> None:
        now = time.monotonic()
        to_remove = [
            uid for uid, idx in self.indexes.items() if now - idx.last_used > self.ttl
        ]
        if to_remove:
            logger.debug("Evicting %d idle indexes", len(to_remove))
        for uid in to_remove:
            self.indexes.pop(uid, None)
            self.cursors.pop(uid, None)
            lock = self.locks.pop(uid, None)
            if lock and lock.locked():
                logger.debug("Lock for user %s remained locked during eviction", uid)

    async def maintenance(self) -> None:
        now = time.monotonic()
        if now < self._next_maintenance:
            return
        self._next_maintenance = now + self.maintenance_interval
        self._evict_idle()
        self._enforce_global_budget()

    async def remove_entries(self, user_id: str, entry_ids: Iterable[str]) -> None:
        ids = [entry_id for entry_id in entry_ids if entry_id]
        if not ids:
            return
        idx = self.indexes.get(user_id)
        if not idx:
            return
        lock = self._get_lock(user_id)
        async with lock:
            idx.remove_entries(ids)
