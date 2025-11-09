from __future__ import annotations

from typing import Any

import pytest

from llamora.app.services.history_serializer import (
    PHASE_LABELS,
    serialize_history_for_view,
)


def _message(
    msg_id: str,
    created_at: str,
    role: str = "user",
    message: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "created_at": created_at,
        "role": role,
        "message": message,
        "meta": meta or {},
        "prompt_tokens": 0,
        "tags": [],
    }


def test_inserts_dividers_for_new_hour_blocks() -> None:
    history = [
        _message("a", "2025-01-02T04:15:00+00:00", "user"),
        _message("b", "2025-01-02T04:45:00+00:00", "assistant"),
        _message("c", "2025-01-02T08:05:00+00:00", "user"),
        _message("d", "2025-01-02T17:35:00+00:00", "assistant"),
    ]

    items = serialize_history_for_view(history, "UTC")
    kinds = [item["item_type"] for item in items]

    assert kinds == [
        "divider",
        "message",
        "message",
        "divider",
        "message",
        "divider",
        "message",
    ]

    first_divider = items[0]
    assert first_divider["label"] == "Night • 04:00"
    assert first_divider["phase"] == "night"

    morning_divider = items[3]
    assert morning_divider["label"] == "Morning • 08:00"
    assert morning_divider["phase"] == "morning"

    evening_divider = items[5]
    assert evening_divider["label"] == "Evening • 17:00"
    assert evening_divider["phase"] == "evening"


@pytest.mark.parametrize(
    "timezone_name, expected_times",
    [
        ("America/New_York", ["01:00", "02:00"]),
        ("Asia/Tokyo", ["14:00", "15:00"]),
    ],
)
def test_respects_timezone_when_bucketing(timezone_name: str, expected_times: list[str]) -> None:
    history = [
        _message("a", "2025-03-15T05:15:00+00:00"),
        _message("b", "2025-03-15T06:05:00+00:00"),
    ]

    items = serialize_history_for_view(history, timezone_name)
    dividers = [item for item in items if item["item_type"] == "divider"]

    assert [divider["time_label"] for divider in dividers] == expected_times
    assert {divider["phase"] for divider in dividers} <= set(PHASE_LABELS)
