"""Deprecated tag side effects.

Invalidations are now coordinated through repository events and
:mod:`llamora.app.services.invalidation_coordinator`.
"""

from __future__ import annotations

from logging import getLogger

logger = getLogger(__name__)


async def after_tag_changed(**_: object) -> None:
    """Compatibility no-op; invalidations now run via event subscribers."""

    logger.debug("after_tag_changed is deprecated; invalidation is event-driven")


async def after_tag_deleted(**_: object) -> None:
    """Compatibility no-op; invalidations now run via event subscribers."""

    logger.debug("after_tag_deleted is deprecated; invalidation is event-driven")


__all__ = ["after_tag_changed", "after_tag_deleted"]
