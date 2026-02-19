"""Helpers for rendering compact activity heatmaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from logging import getLogger
import math

from llamora.app.services.cache_registry import (
    HEATMAP_NAMESPACE,
    heatmap_month_cache_key,
)
from llamora.app.services.crypto import CryptoContext
from llamora.app.services.lockbox_store import LockboxStore
from llamora.app.services.time import local_date

logger = getLogger(__name__)


@dataclass(slots=True)
class ActivityHeatmapCell:
    date: str
    count: int
    level: int
    in_month: bool
    target_entry_id: str | None = None


@dataclass(slots=True)
class ActivityHeatmapWeek:
    days: tuple[ActivityHeatmapCell, ...]


@dataclass(slots=True)
class ActivityHeatmapMonth:
    label: str
    aria_label: str
    year: int
    weeks: tuple[ActivityHeatmapWeek, ...]


@dataclass(slots=True)
class ActivityHeatmapData:
    start: str
    end: str
    max_count: int
    months: tuple[ActivityHeatmapMonth, ...]
    offset: int
    months_count: int
    has_prev: bool
    has_next: bool


def _level_for_count(count: int, max_count: int) -> int:
    if count <= 0 or max_count <= 0:
        return 0
    if max_count <= 1:
        return 1
    return min(4, max(1, math.ceil((count / max_count) * 4)))


def _month_start(target: date) -> date:
    return date(target.year, target.month, 1)


def _month_end(target: date) -> date:
    next_month = (target.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


def _shift_month(target: date, offset: int) -> date:
    year = target.year + (target.month - 1 + offset) // 12
    month = (target.month - 1 + offset) % 12 + 1
    return date(year, month, 1)


def _month_token(target: date) -> str:
    return target.strftime("%Y-%m")


def _parse_cached_month_payload(
    payload: object,
) -> tuple[dict[str, int], dict[str, str]] | None:
    if not isinstance(payload, dict):
        return None
    raw_counts = payload.get("counts")
    if not isinstance(raw_counts, dict):
        return None
    raw_first_entries = payload.get("first_entries")
    if not isinstance(raw_first_entries, dict):
        return None
    parsed: dict[str, int] = {}
    for raw_date, raw_count in raw_counts.items():
        key = str(raw_date or "").strip()
        if not key:
            continue
        try:
            parsed[key] = int(raw_count or 0)
        except (TypeError, ValueError):
            return None
    first_entries: dict[str, str] = {}
    for raw_date, raw_entry_id in raw_first_entries.items():
        key = str(raw_date or "").strip()
        if not key:
            continue
        entry_id = str(raw_entry_id or "").strip()
        if entry_id:
            first_entries[key] = entry_id
    return parsed, first_entries


def _counts_for_month(counts: dict[str, int], month_start: date) -> dict[str, int]:
    prefix = f"{_month_token(month_start)}-"
    return {
        iso_date: count
        for iso_date, count in counts.items()
        if str(iso_date).startswith(prefix)
    }


def _first_entries_for_month(
    first_entries: dict[str, str],
    month_start: date,
) -> dict[str, str]:
    prefix = f"{_month_token(month_start)}-"
    return {
        iso_date: entry_id
        for iso_date, entry_id in first_entries.items()
        if str(iso_date).startswith(prefix)
    }


def build_activity_heatmap(
    counts: dict[str, int],
    *,
    first_entries: dict[str, str] | None = None,
    end: date | None = None,
    months: int = 12,
    offset: int = 0,
    min_date: date | None = None,
) -> ActivityHeatmapData:
    end_date = end or local_date()
    months = max(1, months)
    offset = max(0, offset)

    end_month = _month_start(end_date)
    window_end = _shift_month(end_month, -offset)
    window_start = _shift_month(window_end, -(months - 1))

    max_count = max(counts.values(), default=0)
    month_blocks: list[ActivityHeatmapMonth] = []
    for index in range(months):
        month_start = _shift_month(window_start, index)
        month_end = _month_end(month_start)
        grid_start = month_start - timedelta(days=month_start.weekday())
        grid_end = month_end + timedelta(days=6 - month_end.weekday())
        weeks: list[ActivityHeatmapWeek] = []
        cursor = grid_start
        while cursor <= grid_end:
            days: list[ActivityHeatmapCell] = []
            for offset_day in range(7):
                current = cursor + timedelta(days=offset_day)
                iso_date = current.isoformat()
                in_month = month_start <= current <= month_end
                count = int(counts.get(iso_date, 0)) if in_month else 0
                level = _level_for_count(count, max_count) if in_month else 0
                target_entry_id = None
                if in_month and count > 0 and first_entries:
                    target_entry_id = first_entries.get(iso_date)
                days.append(
                    ActivityHeatmapCell(
                        date=iso_date,
                        count=count,
                        level=level,
                        in_month=in_month,
                        target_entry_id=target_entry_id,
                    )
                )
            weeks.append(ActivityHeatmapWeek(days=tuple(days)))
            cursor += timedelta(days=7)
        month_blocks.append(
            ActivityHeatmapMonth(
                label=month_start.strftime("%b"),
                aria_label=month_start.strftime("%B %Y"),
                year=month_start.year,
                weeks=tuple(weeks),
            )
        )

    min_month = _month_start(min_date) if min_date else None
    if min_month:
        has_prev = window_start > min_month
    else:
        has_prev = True
    has_next = offset > 0

    return ActivityHeatmapData(
        start=window_start.isoformat(),
        end=_month_end(window_end).isoformat(),
        max_count=max_count,
        months=tuple(month_blocks),
        offset=offset,
        months_count=months,
        has_prev=has_prev,
        has_next=has_next,
    )


async def get_tag_activity_heatmap(
    tags_repo,
    ctx: CryptoContext,
    tag_hash: bytes,
    *,
    store: LockboxStore | None = None,
    end: date | None = None,
    months: int = 12,
    offset: int = 0,
    min_date: date | None = None,
) -> ActivityHeatmapData:
    end_date = end or local_date()
    months = max(1, months)
    offset = max(0, offset)
    end_month = _month_start(end_date)
    window_end = _shift_month(end_month, -offset)
    window_start = _shift_month(window_end, -(months - 1))
    month_starts = tuple(_shift_month(window_start, idx) for idx in range(months))
    query_start = window_start.isoformat()
    query_end = _month_end(window_end).isoformat()
    tag_hash_hex = tag_hash.hex()

    counts: dict[str, int] = {}
    first_entries: dict[str, str] = {}
    missing_months: list[date] = []
    if store is not None:
        for month_start in month_starts:
            cache_key = heatmap_month_cache_key(tag_hash_hex, _month_token(month_start))
            try:
                cached_payload = await store.get_json(ctx, HEATMAP_NAMESPACE, cache_key)
            except Exception:
                logger.debug(
                    "heatmap cache read failed user=%s tag=%s month=%s",
                    ctx.user_id,
                    tag_hash_hex,
                    _month_token(month_start),
                    exc_info=True,
                )
                missing_months.append(month_start)
                continue
            month_payload = _parse_cached_month_payload(cached_payload)
            if month_payload is None:
                missing_months.append(month_start)
                continue
            month_counts, month_first_entries = month_payload
            counts.update(month_counts)
            first_entries.update(month_first_entries)

    if store is None or missing_months:
        (
            queried_counts,
            queried_first_entries,
        ) = await tags_repo.get_tag_activity_snapshot(
            ctx.user_id,
            tag_hash,
            query_start,
            query_end,
        )
        counts.update(queried_counts)
        first_entries.update(queried_first_entries)
        if store is not None and missing_months:
            for month_start in missing_months:
                cache_key = heatmap_month_cache_key(
                    tag_hash_hex, _month_token(month_start)
                )
                payload = {
                    "counts": _counts_for_month(queried_counts, month_start),
                    "first_entries": _first_entries_for_month(
                        queried_first_entries, month_start
                    ),
                }
                try:
                    await store.set_json(ctx, HEATMAP_NAMESPACE, cache_key, payload)
                except Exception:
                    logger.debug(
                        "heatmap cache write failed user=%s tag=%s month=%s",
                        ctx.user_id,
                        tag_hash_hex,
                        _month_token(month_start),
                        exc_info=True,
                    )

    return build_activity_heatmap(
        counts,
        first_entries=first_entries,
        end=end_date,
        months=months,
        offset=offset,
        min_date=min_date,
    )
