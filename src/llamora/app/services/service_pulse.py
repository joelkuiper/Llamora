from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from contextlib import suppress
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol, cast

from blinker import Namespace, Signal

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PulseEvent:
    """Snapshot of a published service pulse."""

    topic: str
    payload: Mapping[str, Any]
    timestamp: float

    def as_payload(self) -> dict[str, Any]:
        """Return a mutable copy of the payload."""

        return dict(self.payload)


class PulseListener(Protocol):
    """Callback protocol for service pulse subscribers."""

    def __call__(self, event: PulseEvent) -> Awaitable[None] | None:
        ...


class ServicePulse:
    """Pub/sub helper for broadcasting service metrics and state."""

    def __init__(self) -> None:
        self._latest: dict[str, PulseEvent] = {}
        self._namespace = Namespace()
        self._broadcast_signal = Signal("service_pulse:*")

    def signal(self, topic: str) -> Signal:
        """Return a blinker :class:`Signal` for ``topic``."""

        return self._namespace.signal(topic)

    def emit(self, topic: str, payload: Mapping[str, Any]) -> PulseEvent:
        """Record ``payload`` under ``topic`` and notify subscribers."""

        data = MappingProxyType(dict(payload))
        event = PulseEvent(topic=topic, payload=data, timestamp=time.monotonic())
        self._latest[topic] = event
        self._notify_signal(self.signal(topic), event)
        self._notify_signal(self._broadcast_signal, event)
        return event

    def _notify_signal(self, signal: Signal, event: PulseEvent) -> None:
        for receiver in list(signal.receivers_for(self)):
            try:
                result = receiver(
                    self,
                    event=event,
                    topic=event.topic,
                    payload=event.payload,
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception("Service pulse listener failed for topic %s", event.topic)
                continue
            self._handle_result(result)

    def _handle_result(self, result: Any) -> None:
        if result is None:
            return

        awaitable: Awaitable[Any] | None = None
        if asyncio.iscoroutine(result):
            awaitable = result
        elif asyncio.isfuture(result):
            future = cast(asyncio.Future[Any], result)
            if not future.done():

                def _log_future(fut: asyncio.Future[Any]) -> None:
                    with suppress(Exception):
                        fut.result()

                future.add_done_callback(_log_future)
            return

        if awaitable is None:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(awaitable)
        else:  # pragma: no branch - simple scheduling
            loop.create_task(awaitable)

    def subscribe(
        self,
        listener: PulseListener,
        *,
        topics: Iterable[str] | None = None,
        replay_last: bool = True,
    ) -> Callable[[], None]:
        """Subscribe to pulse events and optionally replay the latest values."""

        topic_list: list[str] | None = None
        if topics is not None:
            topic_list = list(dict.fromkeys(topics))

        def _receiver(sender: Any, *, event: PulseEvent | None = None, **_: Any) -> None:
            if event is None:
                return
            try:
                result = listener(event)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Service pulse listener failed for topic %s", event.topic)
                return
            self._handle_result(result)

        signals: list[Signal]
        if topic_list is None:
            signals = [self._broadcast_signal]
        else:
            signals = [self.signal(topic) for topic in topic_list]

        for sig in signals:
            sig.connect(_receiver, sender=self, weak=False)

        if replay_last:
            if topic_list is None:
                events = list(self._latest.values())
            else:
                events = [
                    event
                    for name, event in self._latest.items()
                    if name in topic_list
                ]
            for event in events:
                try:
                    result = listener(event)
                except Exception:  # pragma: no cover - defensive
                    logger.exception(
                        "Service pulse listener failed during replay for topic %s",
                        event.topic,
                    )
                else:
                    self._handle_result(result)

        def unsubscribe() -> None:
            for sig in signals:
                sig.disconnect(_receiver, sender=self)

        return unsubscribe

    def register(
        self,
        listener: Callable[[str, dict[str, Any]], None],
        *,
        topics: Iterable[str] | None = None,
        replay_last: bool = True,
    ) -> Callable[[], None]:
        """Backward compatible subscription helper for legacy callbacks."""

        def _adapter(event: PulseEvent) -> None:
            listener(event.topic, event.as_payload())

        return self.subscribe(
            _adapter,
            topics=topics,
            replay_last=replay_last,
        )

    def latest(self, topic: str) -> dict[str, Any] | None:
        """Return the most recent payload for ``topic`` if available."""

        event = self._latest.get(topic)
        if event is None:
            return None
        return event.as_payload()

    def latest_event(self, topic: str) -> PulseEvent | None:
        """Return the most recent ``PulseEvent`` for ``topic`` if present."""

        return self._latest.get(topic)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the latest payloads for all topics."""

        return {topic: event.as_payload() for topic, event in self._latest.items()}

    def topics(self) -> set[str]:
        """Return the set of topics that have produced pulses."""

        return set(self._latest.keys())


__all__ = ["PulseEvent", "PulseListener", "ServicePulse"]
