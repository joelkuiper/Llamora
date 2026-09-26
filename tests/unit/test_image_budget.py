"""Images in the prompt budget (PromptBudget.fit_images, estimates).

When a reply doesn't fit, earlier images give way first (oldest first), then
earlier entries; the replied-to entry's own images only if it can't fit with
them on its own.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from llamora.llm.budget import PromptBudget
from llamora.llm.entry_template import (
    ImagePolicy,
    build_entry_messages,
    estimate_entry_messages_tokens,
    select_images,
)
from llamora.llm.tokenizers.tokenizer import history_suffix_token_totals

PER_IMAGE = 1000  # large, so images dominate and the arithmetic is clear
POLICY = ImagePolicy(send=True, max_images=4, tokens_per_image=PER_IMAGE)


def entry(text: str, *ids: str, role: str = "user") -> dict:
    return {"role": role, "text": text, "images": [{"id": i} for i in ids]}


HISTORY = [
    entry("Morning at the market", "m1", "m2"),
    entry("A reply", role="assistant"),
    entry("Evening by the water", "e1"),  # being replied to
]


class StubClient:
    logger = logging.getLogger("test")
    default_generation: dict = {}
    ctx_size = 100_000

    async def _get_token_counts(self, history, context):
        return tuple(history_suffix_token_totals(history, context=context))


def fit(history, policy, *, room: int) -> ImagePolicy:
    budget = PromptBudget(StubClient())
    budget.max_prompt_tokens = lambda params=None: room  # type: ignore[method-assign]
    return asyncio.run(budget.fit_images(history, policy, context={}))


def text_tokens(history) -> int:
    """The prompt without any images (so rooms can be set in image units)."""
    without = ImagePolicy(send=True, max_images=0, tokens_per_image=PER_IMAGE)
    return history_suffix_token_totals(history, context={"image_policy": without})[0]


def shown(history, policy) -> set[str]:
    return select_images(history, policy.max_images)


def test_estimates_count_every_image() -> None:
    messages = build_entry_messages(HISTORY, image_policy=POLICY)
    with_cost = estimate_entry_messages_tokens(messages, tokens_per_image=PER_IMAGE)
    without = estimate_entry_messages_tokens(messages)
    assert with_cost - without == 3 * PER_IMAGE


def test_everything_fits() -> None:
    policy = fit(HISTORY, POLICY, room=text_tokens(HISTORY) + 3 * PER_IMAGE + 200)
    assert shown(HISTORY, policy) == {"e1", "m1", "m2"}


def test_the_oldest_earlier_image_goes_first() -> None:
    policy = fit(HISTORY, POLICY, room=text_tokens(HISTORY) + 2 * PER_IMAGE + 200)
    assert shown(HISTORY, policy) == {"e1", "m2"}


def test_earlier_images_all_go_before_the_entrys_own() -> None:
    policy = fit(HISTORY, POLICY, room=text_tokens(HISTORY) + PER_IMAGE + 200)
    assert shown(HISTORY, policy) == {"e1"}


def test_the_entrys_own_images_stay_while_the_entry_fits_with_them() -> None:
    # A long earlier entry: the whole day doesn't fit even without images, so
    # earlier entries will be trimmed, but the entry fits with its own image.
    history = [
        entry("A long morning. " * 300, "m1"),
        entry("Evening by the water", "e1"),
    ]
    room = text_tokens(history[-1:]) + PER_IMAGE + 100
    assert room < text_tokens(history)
    assert shown(history, fit(history, POLICY, room=room)) == {"e1"}


def test_the_entrys_own_images_go_one_by_one_when_it_cannot_fit() -> None:
    history = [entry("Three photos", "a", "b", "c")]
    room = text_tokens(history) + 2 * PER_IMAGE + 100
    assert shown(history, fit(history, POLICY, room=room)) == {"a", "b"}
    assert fit(history, POLICY, room=text_tokens(history) + 100).max_images == 0


@pytest.mark.parametrize(
    "policy",
    [ImagePolicy(send=False, max_images=4), ImagePolicy(send=True, max_images=0)],
)
def test_nothing_to_fit_leaves_the_policy_alone(policy: ImagePolicy) -> None:
    assert fit(HISTORY, policy, room=10) == policy


def test_trimming_counts_the_images_that_remain() -> None:
    # With images counted, a tight room drops earlier entries sooner.
    counted = {
        "image_policy": ImagePolicy(send=True, max_images=4, tokens_per_image=PER_IMAGE)
    }
    free = {"image_policy": ImagePolicy(send=True, max_images=4, tokens_per_image=0)}
    with_images = history_suffix_token_totals(HISTORY, context=counted)
    without = history_suffix_token_totals(HISTORY, context=free)
    assert with_images[0] - without[0] == 3 * PER_IMAGE
    assert with_images[-1] - without[-1] == PER_IMAGE  # the entry alone: its own
