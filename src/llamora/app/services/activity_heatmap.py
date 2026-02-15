"""Helpers for rendering compact activity heatmaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math

from llamora.app.services.time import local_date


@dataclass(slots=True)
class ActivityHeatmapCell:
    date: str
    count: int
    level: int
    in_month: bool


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


def build_activity_heatmap(
    counts: dict[str, int],
    *,
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
                days.append(
                    ActivityHeatmapCell(
                        date=iso_date,
                        count=count,
                        level=level,
                        in_month=in_month,
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
    user_id: str,
    tag_hash: bytes,
    *,
    end: date | None = None,
    months: int = 12,
    offset: int = 0,
    min_date: date | None = None,
) -> ActivityHeatmapData:
    end_date = end or local_date()
    end_month = _month_start(end_date)
    window_end = _shift_month(end_month, -max(0, offset))
    window_start = _shift_month(window_end, -(max(1, months) - 1))
    counts = await tags_repo.get_tag_activity_counts(
        user_id,
        tag_hash,
        window_start.isoformat(),
        _month_end(window_end).isoformat(),
    )
    return build_activity_heatmap(
        counts,
        end=end_date,
        months=months,
        offset=offset,
        min_date=min_date,
    )
