"""Entry images in the browser: square thumbnails and the lightbox.

Entries are seeded over HTTP (upload, then create the entry with the image
ids); everything asserted is what a person sees and does on the page.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from playwright.sync_api import Locator, Page, expect

from harness import ApiClient, marker, today_utc, wait_for_app
from imaging import encode, halves

WIDE = encode(halves((600, 300), "red", "blue"), "JPEG")
TALL = encode(halves((300, 900), "green", "yellow"), "JPEG")
SQUARE = encode(halves((400, 400), "purple", "orange"), "PNG")


def seed(
    api: ApiClient, images: list[bytes], *, text: str | None = None, day=None
) -> tuple[str, list[str]]:
    ids = [api.upload_image_id(data) for data in images]
    entry_id = api.create_entry(
        day or today_utc(),
        f"Pictures {marker()}" if text is None else text,
        image_ids=ids,
    )
    return entry_id, ids


def open_day(page: Page, day=None) -> None:
    page.goto(f"/d/{(day or today_utc()).isoformat()}")
    wait_for_app(page)


def thumbs(page: Page, entry_id: str) -> Locator:
    return page.locator(f"#entry-images-{entry_id} [data-lightbox-item]")


def lightbox(page: Page) -> Locator:
    return page.locator("#image-lightbox")


def expect_showing(page: Page, image_id: str, position: str | None) -> None:
    box = lightbox(page)
    expect(box).to_have_class(re.compile(r"\bis-open\b"))
    expect(box).to_have_attribute("data-image-id", image_id)
    expect(box.locator(".image-lightbox__image")).to_have_attribute(
        "src", re.compile(rf"/i/{image_id}/display$")
    )
    expect(box.locator(".image-lightbox__counter")).to_have_text(position or "")


def expect_closed(page: Page) -> None:
    expect(lightbox(page)).to_be_hidden()


def ratio(locator: Locator) -> float:
    box = locator.bounding_box()
    assert box and box["height"], box
    return box["width"] / box["height"]


# -- thumbnails -----------------------------------------------------------


def test_thumbnails_are_square_whatever_the_image(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, ids = seed(api, [WIDE, TALL, SQUARE])
    open_day(app_page)

    tiles = thumbs(app_page, entry_id)
    expect(tiles).to_have_count(3)
    for index, image_id in enumerate(ids):
        tile = tiles.nth(index)
        expect(tile).to_have_attribute("data-image-id", image_id)
        box = tile.bounding_box()
        assert box and abs(box["width"] - box["height"]) < 1, box
        assert 100 < box["width"] < 140, box  # about 7.5rem, not the image size
        img = tile.locator("img")
        expect(img).to_have_attribute("src", re.compile(rf"/i/{image_id}/thumb$"))
        app_page.wait_for_function(
            "(img) => img.complete && img.naturalWidth > 0", arg=img.element_handle()
        )


def test_thumbnails_take_their_space_before_loading(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, _ = seed(api, [WIDE, TALL])
    # Hold the thumbnails back: the grid must already have its final size.
    app_page.route("**/i/*/thumb", lambda route: None)
    open_day(app_page)
    entry = app_page.locator(f"#entry-{entry_id}")
    tiles = thumbs(app_page, entry_id)
    expect(tiles).to_have_count(2)

    def layout() -> list[tuple[float, float, float, float]]:
        # Relative to the entry, so page scrolling doesn't count.
        base = entry.bounding_box()
        assert base
        boxes = [tiles.nth(i).bounding_box() for i in range(2)]
        return [
            (b["x"] - base["x"], b["y"] - base["y"], b["width"], b["height"])
            for b in boxes
            if b
        ] + [(0, 0, base["width"], base["height"])]

    app_page.wait_for_function(
        "() => document.getAnimations().every((a) => a.playState !== 'running')"
    )
    before = layout()
    app_page.unroute("**/i/*/thumb")
    for i in range(2):
        img = tiles.nth(i).locator("img")
        img.evaluate("(img) => { img.src = img.src + '?again'; }")
        app_page.wait_for_function(
            "(img) => img.complete && img.naturalWidth > 0", arg=img.element_handle()
        )
    assert layout() == before


def test_a_missing_thumbnail_shows_as_unavailable(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, _ = seed(api, [WIDE])
    app_page.route("**/i/*/thumb", lambda route: route.fulfill(status=404))
    open_day(app_page)
    expect(thumbs(app_page, entry_id).first).to_have_attribute("data-state", "error")


# -- the lightbox ---------------------------------------------------------


def test_opens_at_the_clicked_image_in_its_own_shape(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, ids = seed(api, [WIDE, TALL, SQUARE])
    open_day(app_page)

    thumbs(app_page, entry_id).nth(1).click()

    expect_showing(app_page, ids[1], "2 / 3")
    image = lightbox(app_page).locator(".image-lightbox__image")
    expect(image).to_be_visible()
    assert abs(ratio(image) - 300 / 900) < 0.02
    expect(lightbox(app_page).locator(".image-lightbox__full")).to_have_attribute(
        "href", re.compile(rf"/i/{ids[1]}/full$")
    )
    expect(lightbox(app_page).get_by_role("dialog")).to_have_attribute(
        "aria-label", "Image 2 of 3"
    )


BANNER = encode(halves((600, 200), "red", "blue"), "JPEG")
TOWER = encode(halves((200, 600), "green", "yellow"), "JPEG")


def expect_true_shape(page: Page, image_id: str) -> None:
    """The shown <img>'s rendered box has the image's own shape.

    Compares the box against both the thumbnail's data-width/height and the
    loaded image's naturalWidth/naturalHeight (not just the container).
    """
    box = lightbox(page)
    expect(box).to_have_attribute("data-image-id", image_id)
    image = box.locator(".image-lightbox__image")
    page.wait_for_function(
        """(img) => img.complete && img.naturalWidth > 0
                  && !img.classList.contains('is-changing')
                  && img.src.includes(img.closest('image-lightbox').dataset.imageId)""",
        arg=image.element_handle(),
    )
    shape = image.evaluate(
        """(img) => {
            const rect = img.getBoundingClientRect();
            const thumb = document.querySelector(
                `[data-lightbox-item][data-image-id="${img.closest('image-lightbox').dataset.imageId}"]`);
            return {
                boxW: rect.width, boxH: rect.height,
                cssW: img.clientWidth, cssH: img.clientHeight,
                natW: img.naturalWidth, natH: img.naturalHeight,
                dataW: Number(thumb.dataset.width), dataH: Number(thumb.dataset.height),
                fit: getComputedStyle(img).objectFit,
            };
        }"""
    )
    natural = shape["natW"] / shape["natH"]
    assert abs(shape["dataW"] / shape["dataH"] - natural) < 0.01, shape
    assert abs(shape["boxW"] / shape["boxH"] - natural) < 0.02, shape
    assert abs(shape["cssW"] / shape["cssH"] - natural) < 0.02, shape


def test_every_image_keeps_its_own_shape(app_page: Page, api: ApiClient) -> None:
    # Square first: a box left over from it would squash the others to 1:1.
    entry_id, ids = seed(api, [SQUARE, BANNER, TOWER])
    open_day(app_page)
    tiles = thumbs(app_page, entry_id)

    tiles.nth(0).click()
    expect_true_shape(app_page, ids[0])
    app_page.keyboard.press("ArrowRight")
    expect_true_shape(app_page, ids[1])
    app_page.keyboard.press("ArrowRight")
    expect_true_shape(app_page, ids[2])
    app_page.keyboard.press("ArrowLeft")
    expect_true_shape(app_page, ids[1])

    # Reopening at another image after closing on a different one.
    app_page.keyboard.press("Escape")
    expect_closed(app_page)
    tiles.nth(2).click()
    expect_true_shape(app_page, ids[2])
    app_page.keyboard.press("Escape")
    tiles.nth(0).click()
    expect_true_shape(app_page, ids[0])

    # And after the window changes size.
    app_page.set_viewport_size({"width": 700, "height": 900})
    app_page.keyboard.press("ArrowRight")
    expect_true_shape(app_page, ids[1])
    app_page.set_viewport_size({"width": 1280, "height": 500})
    app_page.keyboard.press("ArrowRight")
    expect_true_shape(app_page, ids[2])
    # Even a mis-sized box would letterbox rather than stretch the image.
    image = lightbox(app_page).locator(".image-lightbox__image")
    assert image.evaluate("(img) => getComputedStyle(img).objectFit") == "contain"


def test_moving_between_images(app_page: Page, api: ApiClient) -> None:
    entry_id, ids = seed(api, [WIDE, TALL, SQUARE])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()
    box = lightbox(app_page)
    prev = box.get_by_role("button", name="Previous image")
    nxt = box.get_by_role("button", name="Next image")

    expect_showing(app_page, ids[0], "1 / 3")
    expect(prev).to_be_disabled()
    nxt.click()
    expect_showing(app_page, ids[1], "2 / 3")
    app_page.keyboard.press("ArrowRight")
    expect_showing(app_page, ids[2], "3 / 3")
    expect(nxt).to_be_disabled()
    app_page.keyboard.press("ArrowRight")  # no wrap-around
    expect_showing(app_page, ids[2], "3 / 3")
    app_page.keyboard.press("ArrowLeft")
    expect_showing(app_page, ids[1], "2 / 3")
    prev.click()
    expect_showing(app_page, ids[0], "1 / 3")
    image = box.locator(".image-lightbox__image")
    expect(image).not_to_have_class(re.compile(r"is-changing"))
    assert abs(ratio(image) - 2.0) < 0.03


def test_a_single_image_has_no_navigation(app_page: Page, api: ApiClient) -> None:
    entry_id, ids = seed(api, [SQUARE])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()

    expect_showing(app_page, ids[0], None)
    expect(lightbox(app_page).get_by_role("button", name="Next image")).to_be_hidden()
    expect(
        lightbox(app_page).get_by_role("button", name="Previous image")
    ).to_be_hidden()


@pytest.mark.parametrize("how", ["escape", "backdrop", "close-button"])
def test_closing_returns_focus_to_the_thumbnail(
    app_page: Page, api: ApiClient, how: str
) -> None:
    entry_id, _ = seed(api, [WIDE, TALL])
    open_day(app_page)
    tile = thumbs(app_page, entry_id).nth(1)
    tile.click()
    expect(lightbox(app_page)).to_have_class(re.compile(r"\bis-open\b"))

    if how == "escape":
        app_page.keyboard.press("Escape")
    elif how == "backdrop":
        app_page.mouse.click(8, 300)  # beside the image, outside the controls
    else:
        lightbox(app_page).get_by_role("button", name="Close").click()

    expect_closed(app_page)
    expect(tile).to_be_focused()


def test_clicking_the_image_itself_keeps_it_open(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, ids = seed(api, [WIDE])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()
    lightbox(app_page).locator(".image-lightbox__image").click()
    expect_showing(app_page, ids[0], None)


def test_focus_stays_inside_while_open(app_page: Page, api: ApiClient) -> None:
    entry_id, _ = seed(api, [WIDE, TALL])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()
    expect(lightbox(app_page)).to_have_class(re.compile(r"\bis-open\b"))
    for _ in range(8):
        app_page.keyboard.press("Tab")
        assert app_page.evaluate(
            "() => document.getElementById('image-lightbox').contains(document.activeElement)"
        )


def test_swiping(app_page: Page, api: ApiClient) -> None:
    entry_id, ids = seed(api, [WIDE, TALL])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()
    expect_showing(app_page, ids[0], "1 / 2")

    def swipe(dx: int) -> None:
        box = lightbox(app_page).locator(".image-lightbox__image").bounding_box()
        assert box
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        app_page.mouse.move(x, y)
        app_page.mouse.down()
        app_page.mouse.move(x + dx, y, steps=5)
        app_page.mouse.up()

    swipe(-120)
    expect_showing(app_page, ids[1], "2 / 2")
    swipe(120)
    expect_showing(app_page, ids[0], "1 / 2")
    swipe(20)  # too short: nothing happens, and it doesn't close
    expect_showing(app_page, ids[0], "1 / 2")


def test_navigating_away_closes_it(app_page: Page, api: ApiClient) -> None:
    entry_id, _ = seed(api, [WIDE])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()
    expect(lightbox(app_page)).to_have_class(re.compile(r"\bis-open\b"))

    app_page.evaluate(
        """() => htmx.ajax('GET', '/e/today', {
            target: '#content-wrapper', swap: 'outerHTML', source: document.body})"""
    )

    expect_closed(app_page)
    assert not app_page.evaluate(
        "() => Array.from(document.body.children).some((el) => el.inert)"
    )


def test_works_in_the_tags_view(app_page: Page, api: ApiClient) -> None:
    entry_id, ids = seed(api, [WIDE, TALL])
    tag = marker("frame")
    api.add_tag(entry_id, tag)

    app_page.goto(f"/t/{tag}")
    wait_for_app(app_page)
    tiles = thumbs(app_page, entry_id)
    expect(tiles).to_have_count(2)
    tiles.nth(1).click()

    expect_showing(app_page, ids[1], "2 / 2")
    app_page.keyboard.press("ArrowLeft")
    expect_showing(app_page, ids[0], "1 / 2")


# -- removing -------------------------------------------------------------


def confirm_modal(page: Page) -> Locator:
    return page.locator("#confirm-modal")


def test_removing_asks_first_and_keep_changes_nothing(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, ids = seed(api, [WIDE, TALL])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()

    lightbox(app_page).get_by_role("button", name="Remove image from entry").click()
    modal = confirm_modal(app_page)
    expect(modal).to_have_class(re.compile(r"\bis-open\b"))
    expect(modal.locator("#confirm-modal-title")).to_have_text("Remove image?")
    modal.get_by_role("button", name="Keep").click()

    expect(modal).to_be_hidden()
    expect_showing(app_page, ids[0], "1 / 2")
    expect(thumbs(app_page, entry_id)).to_have_count(2)
    assert api.get_image(ids[0], "thumb").status_code == 200


def test_escape_in_the_confirmation_leaves_the_lightbox_open(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, ids = seed(api, [WIDE, TALL])
    open_day(app_page)
    thumbs(app_page, entry_id).first.click()
    lightbox(app_page).get_by_role("button", name="Remove image from entry").click()
    expect(confirm_modal(app_page)).to_have_class(re.compile(r"\bis-open\b"))

    app_page.keyboard.press("Escape")

    expect(confirm_modal(app_page)).to_be_hidden()
    expect_showing(app_page, ids[0], "1 / 2")


def test_removing_an_image_moves_to_the_next(app_page: Page, api: ApiClient) -> None:
    entry_id, ids = seed(api, [WIDE, TALL, SQUARE])
    open_day(app_page)
    thumbs(app_page, entry_id).nth(1).click()

    lightbox(app_page).get_by_role("button", name="Remove image from entry").click()
    confirm_modal(app_page).get_by_role("button", name="Remove").click()

    expect_showing(app_page, ids[2], "2 / 2")
    tiles = thumbs(app_page, entry_id)
    expect(tiles).to_have_count(2)
    expect(tiles.nth(0)).to_have_attribute("data-image-id", ids[0])
    expect(tiles.nth(1)).to_have_attribute("data-image-id", ids[2])
    assert api.get_image(ids[1], "thumb").status_code == 404

    # Removing the last one of the entry closes the lightbox; the entry stays.
    app_page.keyboard.press("Escape")
    thumbs(app_page, entry_id).first.click()
    for _ in range(2):
        lightbox(app_page).get_by_role("button", name="Remove image from entry").click()
        confirm_modal(app_page).get_by_role("button", name="Remove").click()
        expect(confirm_modal(app_page)).to_be_hidden()
    expect_closed(app_page)
    expect(thumbs(app_page, entry_id)).to_have_count(0)
    expect(app_page.locator(f"#entry-{entry_id}")).to_be_visible()


def test_removing_the_only_image_of_an_entry_without_text_deletes_it(
    app_page: Page, api: ApiClient
) -> None:
    entry_id, ids = seed(api, [SQUARE], text="")
    open_day(app_page)
    expect(app_page.locator(f"#entry-images-{entry_id}")).to_have_attribute(
        "data-textless", "true"
    )
    thumbs(app_page, entry_id).first.click()

    lightbox(app_page).get_by_role("button", name="Remove image from entry").click()
    modal = confirm_modal(app_page)
    expect(modal.locator("#confirm-modal-title")).to_have_text("Delete entry?")
    expect(modal.locator("#confirm-modal-message")).to_contain_text("no text")
    modal.get_by_role("button", name="Delete entry").click()

    expect_closed(app_page)
    expect(app_page.locator(f"#entry-{entry_id}")).to_have_count(0)
    open_day(app_page)
    expect(app_page.locator(f"#entry-{entry_id}")).to_have_count(0)
    assert api.get_image(ids[0], "thumb").status_code == 404


def test_removing_works_on_a_past_day(app_page: Page, api: ApiClient) -> None:
    day = today_utc() - timedelta(days=3)
    entry_id, ids = seed(api, [WIDE, TALL], day=day)
    open_day(app_page, day)
    thumbs(app_page, entry_id).first.click()

    lightbox(app_page).get_by_role("button", name="Remove image from entry").click()
    confirm_modal(app_page).get_by_role("button", name="Remove").click()

    expect_showing(app_page, ids[1], None)
    expect(thumbs(app_page, entry_id)).to_have_count(1)
