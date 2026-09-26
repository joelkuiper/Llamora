"""Unsent images are part of the draft: they survive reloads and midnight.

Uploads are stored on the server as soon as they're added, so a draft only
needs their ids.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date

from playwright.sync_api import Locator, Page, expect

from harness import ApiClient, marker, wait_for_app, wait_for_htmx_idle
from test_dates_rollover import cross_midnight, expect_day
from test_images_attach import attached_ids, file, pick, png, send, tray, wait_uploaded
from timekit import Diary, at

Session = tuple[Page, ApiClient]
DiaryAt = Callable[..., Diary]
TZ = "America/Los_Angeles"


def reload(page: Page) -> None:
    page.reload()
    wait_for_app(page)


def done_tiles(page: Page) -> Locator:
    return page.locator('image-attach .image-attach__tile[data-state="done"]')


def stored_draft(page: Page, day: str):
    raw = page.evaluate("key => sessionStorage.getItem(key)", f"llamora:draft:{day}")
    return json.loads(raw)["v"] if raw else None


def today_of(page: Page) -> str:
    return page.locator("#entries").get_attribute("data-date") or ""


def test_text_and_images_survive_a_reload(fresh_session: Session) -> None:
    page, _ = fresh_session
    text = f"Half-written, with pictures {marker()}"
    page.locator("#entry-text").fill(text)
    pick(page, file("a.png", png("red")), file("b.png", png("blue")))
    wait_uploaded(page, 2)
    ids = attached_ids(page)
    assert stored_draft(page, today_of(page)) == {"text": text, "images": ids}

    reload(page)

    expect(page.locator("#entry-text")).to_have_value(text)
    expect(done_tiles(page)).to_have_count(2)
    assert attached_ids(page) == ids
    expect(tray(page).first.locator("img")).to_have_attribute(
        "src", re.compile(rf"/i/{ids[0]}/thumb$")
    )
    expect(page.locator("#send-btn")).to_be_enabled()

    entry = send(page)
    assert (
        entry.locator("[data-image-id]").evaluate_all(
            "els => els.map(e => e.dataset.imageId)"
        )
        == ids
    )
    reload(page)  # sent: the draft is gone
    expect(tray(page)).to_have_count(0)
    expect(page.locator("#entry-text")).to_have_value("")


def test_images_alone_are_a_draft(fresh_session: Session) -> None:
    page, _ = fresh_session
    pick(page, file())
    wait_uploaded(page, 1)
    (image_id,) = attached_ids(page)

    reload(page)

    expect(done_tiles(page)).to_have_count(1)
    assert attached_ids(page) == [image_id]


def test_a_removed_image_does_not_come_back(fresh_session: Session) -> None:
    page, _ = fresh_session
    pick(page, file("a.png", png("red")), file("b.png", png("blue")))
    wait_uploaded(page, 2)
    _, kept = attached_ids(page)

    tray(page).first.get_by_role("button", name="Remove image").click()
    expect(tray(page)).to_have_count(1)
    reload(page)

    expect(done_tiles(page)).to_have_count(1)
    assert attached_ids(page) == [kept]


def test_an_image_swept_meanwhile_is_dropped_quietly(fresh_session: Session) -> None:
    page, api = fresh_session
    page.locator("#entry-text").fill("Still here")
    pick(page, file())
    wait_uploaded(page, 1)
    (image_id,) = attached_ids(page)
    # Stands in for the server sweeping an upload that waited too long.
    assert api.delete_image(image_id).status_code == 204

    reload(page)

    expect(page.locator("#entry-text")).to_have_value("Still here")
    expect(tray(page)).to_have_count(0)  # no broken or failed tile
    expect(
        page.locator('image-attach .image-attach__tile[data-state="failed"]')
    ).to_have_count(0)
    page.wait_for_function(
        "day => JSON.parse(sessionStorage.getItem('llamora:draft:' + day)).v.images.length === 0",
        arg=today_of(page),
    )


def test_drafts_from_before_images_still_restore(fresh_session: Session) -> None:
    page, _ = fresh_session
    day = today_of(page)
    page.evaluate(
        """([key, text]) => sessionStorage.setItem(key, JSON.stringify({ v: text, e: null }))""",
        [f"llamora:draft:{day}", "An old plain-text draft"],
    )

    reload(page)

    expect(page.locator("#entry-text")).to_have_value("An old plain-text draft")
    expect(tray(page)).to_have_count(0)


def test_unsent_images_move_to_the_new_day_at_midnight(diary_at: DiaryAt) -> None:
    diary = diary_at(tz=TZ, at=at(TZ, 2026, 3, 10, 23, 58))
    page = diary.page
    text = f"Past midnight, still choosing photos {marker()}"
    page.locator("#entry-text").fill(text)
    pick(page, file("a.png", png("red")), file("b.png", png("blue")))
    wait_uploaded(page, 2)
    ids = attached_ids(page)

    cross_midnight(page, by="00:03:00")

    expect_day(page, date(2026, 3, 11))
    expect(page.locator("#entry-text")).to_have_value(text)
    expect(done_tiles(page)).to_have_count(2)
    assert attached_ids(page) == ids
    assert stored_draft(page, "2026-03-10") is None

    entry = send(page)
    wait_for_htmx_idle(page)
    expect(entry.locator(".entry-images img")).to_have_count(2)
    reload(page)
    expect_day(page, date(2026, 3, 11))
    expect(
        page.locator("#entries .entry.user", has_text="still choosing photos").locator(
            ".entry-images img"
        )
    ).to_have_count(2)
