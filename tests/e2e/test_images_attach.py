"""Attaching images in the entry form: picker, drops, pastes, the tray."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Locator, Page, Request, Route, expect

from harness import ApiClient, marker, wait_for_app, wait_for_htmx_idle
from imaging import encode, halves

Session = tuple[Page, ApiClient]


def png(colour: str = "red", size=(60, 40)) -> bytes:
    return encode(halves(size, left=colour, right="white"), "PNG")


def file(name: str = "photo.png", data: bytes | None = None, mime: str = "image/png"):
    return {
        "name": name,
        "mimeType": mime,
        "buffer": data if data is not None else png(),
    }


@pytest.fixture
def session(fresh_session: Session) -> Iterator[Page]:
    page, _ = fresh_session
    yield page


def tray(page: Page) -> Locator:
    return page.locator("image-attach .image-attach__tile")


def attached_ids(page: Page) -> list[str]:
    return page.locator('image-attach input[name="image_ids"]').evaluate_all(
        "inputs => inputs.map(i => i.value)"
    )


def pick(page: Page, *files) -> None:
    page.locator("image-attach input[type=file]").set_input_files(list(files))


def wait_uploaded(page: Page, count: int) -> None:
    expect(
        page.locator('image-attach .image-attach__tile[data-state="done"]')
    ).to_have_count(count)


def send(page: Page, text: str = "") -> Locator:
    """Send the form and return the new entry."""
    if text:
        page.locator("#entry-text").fill(text)
    before = page.locator("#entries .entry.user").count()
    page.locator("#send-btn").click()
    expect(page.locator("#entries .entry.user")).to_have_count(before + 1)
    wait_for_htmx_idle(page)
    return page.locator("#entries .entry.user").last


def entry_image_ids(entry: Locator) -> list[str]:
    return entry.locator("[data-image-id]").evaluate_all(
        "buttons => buttons.map(b => b.dataset.imageId)"
    )


def dispatch_files(page: Page, target: str, event: str, files, *, kind: str = "drag"):
    """Fire a drag or paste event carrying files (a real DataTransfer)."""
    payload = [
        {
            "name": f["name"],
            "mime": f["mimeType"],
            "b64": base64.b64encode(f["buffer"]).decode(),
        }
        for f in files
    ]
    page.evaluate(
        """([selector, type, files, kind]) => {
          const dt = new DataTransfer();
          for (const f of files) {
            const bytes = Uint8Array.from(atob(f.b64), c => c.charCodeAt(0));
            dt.items.add(new File([bytes], f.name, { type: f.mime }));
          }
          const init = { bubbles: true, cancelable: true };
          const ev = kind === "paste"
            ? new ClipboardEvent(type, { ...init, clipboardData: dt })
            : new DragEvent(type, { ...init, dataTransfer: dt });
          document.querySelector(selector).dispatchEvent(ev);
        }""",
        [target, event, payload, kind],
    )


def dispatch_text_drag(page: Page, event: str) -> None:
    page.evaluate(
        """type => {
          const dt = new DataTransfer();
          dt.setData("text/plain", "just words");
          document.body.dispatchEvent(
            new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
        }""",
        event,
    )


overlay_visible = re.compile(r"\bis-visible\b")


# -- the picker -----------------------------------------------------------


def test_the_attach_button_opens_the_picker_and_images_are_sent(session: Page) -> None:
    page = session
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Attach images").click()
    assert chooser.value.is_multiple()
    chooser.value.set_files([file("a.png", png("red")), file("b.png", png("blue"))])

    wait_uploaded(page, 2)
    ids = attached_ids(page)
    entry = send(page, f"Two pictures {marker()}")

    assert entry_image_ids(entry) == ids  # in the order they were added
    thumbs = entry.locator(".entry-images img")
    expect(thumbs).to_have_count(2)
    expect(thumbs.first).to_have_attribute("src", re.compile(rf"/i/{ids[0]}/thumb$"))
    assert thumbs.first.evaluate("img => img.complete && img.naturalWidth > 0")
    expect(tray(page)).to_have_count(0)  # the tray empties after sending
    assert attached_ids(page) == []


def test_an_entry_of_only_images_is_sent_with_enter(session: Page) -> None:
    page = session
    send_button = page.locator("#send-btn")
    pick(page, file())
    wait_uploaded(page, 1)
    expect(send_button).to_be_enabled()

    before = page.locator("#entries .entry.user").count()
    page.locator("#entry-text").press("Enter")

    expect(page.locator("#entries .entry.user")).to_have_count(before + 1)
    wait_for_htmx_idle(page)
    expect(
        page.locator("#entries .entry.user").last.locator(".entry-images img")
    ).to_have_count(1)


def test_send_waits_for_uploads_to_finish(session: Page) -> None:
    page = session
    held: list[Route] = []
    page.route("**/i", lambda route: held.append(route))
    page.locator("#entry-text").fill("Waiting on a picture")
    pick(page, file())

    expect(tray(page).first).to_have_attribute("data-state", "uploading")
    send_button = page.locator("#send-btn")
    expect(send_button).to_be_disabled()
    expect(send_button).to_have_attribute("data-tooltip-title", "Waiting for images…")
    page.locator("#entry-text").press("Enter")  # also held back
    expect(
        page.locator("#entries .entry.user", has_text="Waiting on a picture")
    ).to_have_count(0)

    for _ in range(50):  # route callbacks run while Playwright waits
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "the upload never reached the network"
    held[0].continue_()
    wait_uploaded(page, 1)
    expect(send_button).to_be_enabled()
    expect(send_button).not_to_have_attribute("data-tooltip-title", re.compile(".+"))

    entry = send(page)
    expect(entry.locator(".entry-images img")).to_have_count(1)


# -- refusals -------------------------------------------------------------


def test_a_file_the_server_refuses_shows_why_and_is_not_sent(session: Page) -> None:
    page = session
    pick(page, file("fake.png", b"definitely not a png", "image/png"))

    failed = page.locator('image-attach .image-attach__tile[data-state="failed"]')
    expect(failed).to_have_count(1)
    expect(failed).to_contain_text("Not an image")
    expect(failed).to_have_attribute("aria-label", re.compile("Not an image"))

    entry = send(page, f"Words only {marker()}")  # failed tiles don't block
    expect(entry.locator(".entry-images img")).to_have_count(0)


def test_non_image_files_are_skipped_with_a_note(session: Page) -> None:
    page = session
    pick(page, file("notes.txt", b"hello", "text/plain"), file("ok.png"))

    expect(tray(page)).to_have_count(1)
    expect(page.locator(".image-attach__note")).to_have_text(
        "Only images can be attached."
    )


def test_too_large_files_fail_without_uploading(session: Page) -> None:
    page = session
    page.locator("image-attach").evaluate("el => { el.dataset.maxBytes = '1000'; }")
    uploads: list[Request] = []
    page.on("request", lambda r: uploads.append(r) if r.url.endswith("/i") else None)

    pick(page, file("big.png", png(size=(400, 400))))

    failed = page.locator('image-attach .image-attach__tile[data-state="failed"]')
    expect(failed).to_contain_text("Too large")
    assert uploads == []


def test_at_most_eight_images(session: Page) -> None:
    page = session
    pick(page, *[file(f"{i}.png", png(size=(20 + i, 20))) for i in range(9)])

    expect(tray(page)).to_have_count(8)
    expect(page.locator(".image-attach__note")).to_have_text(
        "An entry can have up to 8 images."
    )
    wait_uploaded(page, 8)
    pick(page, file())  # still full
    expect(tray(page)).to_have_count(8)


# -- removing (unstaging) -------------------------------------------------


def test_the_remove_button_is_always_visible_and_deletes_the_upload(
    session: Page,
) -> None:
    page = session
    pick(page, file("a.png", png("red")), file("b.png", png("blue")))
    wait_uploaded(page, 2)
    first_id, second_id = attached_ids(page)

    remove = tray(page).first.get_by_role("button", name="Remove image")
    expect(remove).to_be_visible()  # without hovering
    assert float(remove.evaluate("b => getComputedStyle(b).opacity")) == 1.0
    box = remove.evaluate(
        "b => { const r = b.getBoundingClientRect(); return [r.width, r.height]; }"
    )
    assert min(box) >= 20  # the ::before extends the hit area further

    with page.expect_request(
        lambda r: r.method == "DELETE" and r.url.endswith(f"/i/{first_id}")
    ):
        remove.click()

    expect(tray(page)).to_have_count(1)
    assert attached_ids(page) == [second_id]
    expect(tray(page).first).to_be_focused()  # focus moves to the next tile
    assert page.request.get(f"/i/{first_id}/thumb").status == 404
    assert page.request.get(f"/i/{second_id}/thumb").status == 200


def test_removing_with_the_keyboard(session: Page) -> None:
    page = session
    pick(page, file())
    wait_uploaded(page, 1)
    (image_id,) = attached_ids(page)

    tray(page).first.focus()
    page.keyboard.press("Delete")

    expect(tray(page)).to_have_count(0)
    expect(page.locator("#entry-text")).to_be_focused()
    page.wait_for_timeout(300)
    assert page.request.get(f"/i/{image_id}/thumb").status == 404


def test_removing_an_upload_in_progress_cancels_it(session: Page) -> None:
    page = session
    held: list[Route] = []
    page.route("**/i", lambda route: held.append(route))
    pick(page, file())
    expect(tray(page).first).to_have_attribute("data-state", "uploading")

    tray(page).first.get_by_role("button", name="Remove image").click()

    expect(tray(page)).to_have_count(0)
    expect(page.locator("#send-btn")).to_be_disabled()  # nothing to send
    for route in held:
        try:
            route.continue_()
        except Exception:
            pass  # the request was aborted
    page.unroute("**/i")
    entry = send(page, f"No picture after all {marker()}")
    expect(entry.locator(".entry-images img")).to_have_count(0)


# -- drag and drop, paste -------------------------------------------------


def test_dragging_files_shows_the_overlay(session: Page) -> None:
    page = session
    overlay = page.locator(".image-attach__overlay")
    expect(overlay).not_to_have_class(overlay_visible)

    dispatch_files(page, "body", "dragenter", [file()])
    expect(overlay).to_have_class(overlay_visible)
    expect(overlay).to_contain_text("Drop images to attach")
    # Moving over a child (enter it, leave the previous) keeps it up.
    dispatch_files(page, "#entry-text", "dragenter", [file()])
    dispatch_files(page, "body", "dragleave", [file()])
    expect(overlay).to_have_class(overlay_visible)

    dispatch_files(page, "#entry-text", "dragleave", [file()])
    expect(overlay).not_to_have_class(overlay_visible)

    dispatch_files(page, "body", "dragenter", [file()])
    page.keyboard.press("Escape")
    expect(overlay).not_to_have_class(overlay_visible)


def test_dragging_text_does_not_show_the_overlay(session: Page) -> None:
    page = session
    dispatch_text_drag(page, "dragenter")
    page.wait_for_timeout(200)
    expect(page.locator(".image-attach__overlay")).not_to_have_class(overlay_visible)


def test_dropping_images_anywhere_attaches_them_in_order(session: Page) -> None:
    page = session
    files = [
        file("1.png", png("red")),
        file("2.png", png("green")),
        file("3.png", png("blue")),
    ]
    dispatch_files(page, "body", "dragenter", files)
    dispatch_files(page, "#entries", "drop", files)

    expect(page.locator(".image-attach__overlay")).not_to_have_class(overlay_visible)
    wait_uploaded(page, 3)
    ids = attached_ids(page)
    entry = send(page, f"Dropped {marker()}")
    assert entry_image_ids(entry) == ids
    assert page.url.endswith("/d/today")  # the browser didn't open a file


def test_pasting_an_image_attaches_it(session: Page) -> None:
    page = session
    textarea = page.locator("#entry-text")
    textarea.fill("Screenshot: ")

    dispatch_files(page, "#entry-text", "paste", [file("screenshot.png")], kind="paste")

    wait_uploaded(page, 1)
    expect(textarea).to_have_value("Screenshot: ")  # text untouched


def test_attaching_leaves_the_rest_of_the_page_working(session: Page) -> None:
    page = session
    pick(page, file())
    wait_uploaded(page, 1)
    page.reload()
    wait_for_app(page)
    expect(page.locator("#entry-text")).to_be_enabled()
    expect(page.get_by_role("button", name="Attach images")).to_be_enabled()
