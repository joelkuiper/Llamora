"""Editing an entry's images.

The edit form shows the entry's images as a tray. Taking an existing image
out only applies when the edit is saved (cancelling keeps it); images added
during the edit upload straight away; Save needs text or images and waits
for uploads.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from playwright.sync_api import Locator, Page, Route, expect

from harness import ApiClient, marker, wait_for_app, wait_for_htmx_idle
from test_images_attach import dispatch_files, file, png

Session = tuple[Page, ApiClient]
overlay_visible = re.compile(r"\bis-visible\b")


def entry_with_images(
    page: Page, api: ApiClient, count: int, *, text: str | None = None
) -> tuple[Locator, list[str]]:
    from datetime import date

    ids = [
        api.upload_image_id(png(colour)) for colour in ["red", "green", "blue"][:count]
    ]
    day = date.fromisoformat(page.locator("#entries").get_attribute("data-date") or "")
    entry_id = api.create_entry(
        day, f"Pictures {marker()}" if text is None else text, image_ids=ids
    )
    page.reload()
    wait_for_app(page)
    return page.locator(f"#entry-{entry_id}"), ids


def grid_ids(entry: Locator) -> list[str]:
    return entry.locator(".entry-images [data-image-id]").evaluate_all(
        "els => els.map(e => e.dataset.imageId)"
    )


def edit_tray(entry: Locator) -> Locator:
    return entry.locator(".entry-edit-form .image-attach__tile")


def edit_ids(entry: Locator) -> list[str]:
    return entry.locator('.entry-edit-form input[name="image_ids"]').evaluate_all(
        "inputs => inputs.map(i => i.value)"
    )


def start_editing(entry: Locator) -> None:
    entry.get_by_role("button", name="Edit entry").click()
    expect(entry.locator(".entry-edit-form")).to_be_visible()


def save(entry: Locator, page: Page) -> None:
    entry.get_by_role("button", name="Save entry").click()
    expect(entry.locator(".entry-edit-form")).to_have_count(0)
    wait_for_htmx_idle(page)


def cancel(entry: Locator, page: Page) -> None:
    entry.locator(".entry-edit-cancel").click()
    expect(entry.locator(".entry-edit-form")).to_have_count(0)
    wait_for_htmx_idle(page)


def eventually(check: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return check()


def test_editing_shows_the_images_in_a_tray(fresh_session: Session) -> None:
    page, api = fresh_session
    entry, ids = entry_with_images(page, api, 2)

    start_editing(entry)

    expect(edit_tray(entry)).to_have_count(2)
    assert edit_ids(entry) == ids
    expect(entry.locator(".entry-images")).to_be_hidden()  # the tray takes over
    attach = entry.locator(".entry-edit-strip").get_by_role(
        "button", name="Attach image"
    )
    expect(attach).to_be_visible()
    expect(
        entry.locator(".entry-edit-strip").get_by_role("button", name="Save entry")
    ).to_be_enabled()

    cancel(entry, page)
    expect(entry.locator(".entry-images")).to_be_visible()
    assert grid_ids(entry) == ids


def test_removing_an_existing_image_waits_for_save(fresh_session: Session) -> None:
    page, api = fresh_session
    entry, (first, second) = entry_with_images(page, api, 2)
    deletes: list[str] = []
    page.on(
        "request", lambda r: deletes.append(r.url) if r.method == "DELETE" else None
    )

    start_editing(entry)
    edit_tray(entry).first.get_by_role("button", name="Remove image").click()
    expect(edit_tray(entry)).to_have_count(1)
    assert edit_ids(entry) == [second]
    assert deletes == []  # nothing is deleted yet
    expect(entry.locator(".entry-edit-form")).to_be_visible()  # no save on blur

    cancel(entry, page)
    assert grid_ids(entry) == [first, second]  # cancelling keeps it

    start_editing(entry)
    edit_tray(entry).first.get_by_role("button", name="Remove image").click()
    save(entry, page)

    assert grid_ids(entry) == [second]
    assert eventually(lambda: page.request.get(f"/i/{first}/thumb").status == 404)
    page.reload()
    wait_for_app(page)
    assert grid_ids(page.locator(f"#{entry.get_attribute('id')}")) == [second]


def test_adding_an_image_with_the_attach_button(fresh_session: Session) -> None:
    page, api = fresh_session
    entry, ids = entry_with_images(page, api, 1)

    start_editing(entry)
    with page.expect_file_chooser() as chooser:
        entry.get_by_role("button", name="Attach image").click()
    chooser.value.set_files([file("new.png", png("purple"))])
    expect(
        entry.locator('.entry-edit-form .image-attach__tile[data-state="done"]')
    ).to_have_count(2)
    expect(entry.locator(".entry-edit-form")).to_be_visible()  # still editing
    new_ids = edit_ids(entry)
    assert new_ids[0] == ids[0] and len(new_ids) == 2

    save(entry, page)

    assert grid_ids(entry) == new_ids


def test_an_image_added_during_the_edit_is_deleted_when_removed(
    fresh_session: Session,
) -> None:
    page, api = fresh_session
    entry, _ = entry_with_images(page, api, 1)
    start_editing(entry)
    entry.locator(".entry-edit-form image-attach input[type=file]").set_input_files(
        [file("added.png", png("orange"))]
    )
    expect(
        entry.locator('.entry-edit-form .image-attach__tile[data-state="done"]')
    ).to_have_count(2)
    added = edit_ids(entry)[1]

    with page.expect_request(
        lambda r: r.method == "DELETE" and r.url.endswith(f"/i/{added}")
    ):
        edit_tray(entry).last.get_by_role("button", name="Remove image").click()

    expect(edit_tray(entry)).to_have_count(1)


def test_dropping_on_the_edit_form_attaches_to_that_entry(
    fresh_session: Session,
) -> None:
    page, api = fresh_session
    entry, _ = entry_with_images(page, api, 1)
    start_editing(entry)
    edit_overlay = entry.locator(".image-attach__overlay")
    page_overlay = page.locator("#entry-form .image-attach__overlay")
    edit_form = f"#{entry.get_attribute('id')} .entry-edit-form"

    dispatch_files(page, edit_form, "dragenter", [file()])
    expect(edit_overlay).to_have_class(overlay_visible)
    expect(page_overlay).not_to_have_class(overlay_visible)

    dispatch_files(
        page, f"{edit_form} textarea", "drop", [file("dropped.png", png("teal"))]
    )

    expect(
        entry.locator('.entry-edit-form .image-attach__tile[data-state="done"]')
    ).to_have_count(2)
    expect(page.locator("#entry-form .image-attach__tile")).to_have_count(0)
    expect(edit_overlay).not_to_have_class(overlay_visible)


def test_save_needs_text_or_images_and_waits_for_uploads(
    fresh_session: Session,
) -> None:
    page, api = fresh_session
    entry, _ = entry_with_images(page, api, 1)
    save_button = entry.get_by_role("button", name="Save entry")
    start_editing(entry)

    entry.locator(".entry-edit-area").fill("")
    expect(save_button).to_be_enabled()  # images alone are enough
    edit_tray(entry).first.get_by_role("button", name="Remove image").click()
    expect(save_button).to_be_disabled()  # nothing at all is not

    held: list[Route] = []
    page.route("**/i", lambda route: held.append(route))
    entry.locator(".entry-edit-area").fill("Words again")
    entry.locator(".entry-edit-form image-attach input[type=file]").set_input_files(
        [file()]
    )
    expect(edit_tray(entry).first).to_have_attribute("data-state", "uploading")
    expect(save_button).to_be_disabled()  # waiting for the upload

    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(100)
    held[0].continue_()
    expect(save_button).to_be_enabled()
    page.unroute("**/i")
    save(entry, page)
    expect(entry.locator(".entry-images [data-image-id]")).to_have_count(1)
    expect(entry.locator(".markdown-body")).to_have_text("Words again")


def test_an_entry_of_only_images_can_be_edited(fresh_session: Session) -> None:
    page, api = fresh_session
    entry, ids = entry_with_images(page, api, 2, text="")

    start_editing(entry)
    edit_tray(entry).last.get_by_role("button", name="Remove image").click()
    save(entry, page)

    assert grid_ids(entry) == ids[:1]
