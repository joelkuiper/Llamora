"""Helpers for preparing chat history items for rendering."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from zoneinfo import ZoneInfo

from .time import part_of_day


logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")


PHASE_LABELS: dict[str, str] = {
    "night": "Night",
    "early-morning": "Early Morning",
    "morning": "Morning",
    "afternoon": "Afternoon",
    "evening": "Evening",
    "late-night": "Late Night",
}


@dataclass(slots=True)
class HistoryDivider:
    """Presentation metadata describing a divider element."""

    block_id: str
    phase: str
    phase_label: str
    time_label: str
    label: str
    datetime_iso: str
    accessible_label: str
    sr_skip: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_type": "divider",
            "id": self.block_id,
            "phase": self.phase,
            "phase_label": self.phase_label,
            "time_label": self.time_label,
            "label": self.label,
            "datetime": self.datetime_iso,
            "accessible_label": self.accessible_label,
            "sr_skip": self.sr_skip,
        }


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            logger.debug("Unable to parse created_at '%s'", value)
            dt = datetime.now(UTC)
    else:
        dt = datetime.now(UTC)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _resolve_timezone(name: str | None) -> ZoneInfo:
    if not name:
        return UTC

    try:
        return ZoneInfo(name)
    except Exception:
        logger.debug("Invalid timezone '%s' for history divider rendering", name)
        return UTC


def _divider_from_datetime(block_dt: datetime) -> HistoryDivider:
    phase = part_of_day(block_dt)
    phase_label = PHASE_LABELS.get(phase, phase.replace("-", " ").title())
    time_label = block_dt.strftime("%H:%M")
    label = f"{phase_label} • {time_label}"
    accessible_label = f"{phase_label} at {time_label}"
    block_id = f"divider-{block_dt.strftime('%Y%m%dT%H')}"
    return HistoryDivider(
        block_id=block_id,
        phase=phase,
        phase_label=phase_label,
        time_label=time_label,
        label=label,
        datetime_iso=block_dt.isoformat(),
        accessible_label=accessible_label,
    )


def serialize_history_for_view(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    timezone_name: str | None,
) -> list[dict[str, Any]]:
    """Expand ``history`` with divider entries for display.

    ``history`` is expected to be ordered chronologically. A divider entry is
    inserted whenever the local hour bucket changes. Returned entries include an
    ``item_type`` key to distinguish divider metadata from message dictionaries.
    """

    tz = _resolve_timezone(timezone_name)
    items: list[dict[str, Any]] = []
    last_block: tuple[int, int] | None = None

    for entry in history:
        message = dict(entry)
        created_at = _parse_datetime(message.get("created_at"))
        local_dt = created_at.astimezone(tz)
        block_dt = local_dt.replace(minute=0, second=0, microsecond=0)
        block_key = (block_dt.date(), block_dt.hour)

        if last_block != block_key:
            divider = _divider_from_datetime(block_dt)
            items.append(divider.as_dict())
            last_block = block_key

        message["item_type"] = "message"
        items.append(message)

    return items
