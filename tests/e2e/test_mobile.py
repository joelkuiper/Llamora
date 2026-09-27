"""On a phone (an emulated iPhone 13: small screen, touch, no hover).

Checks what desktop tests can't: nothing needs hover, fields don't trigger
iOS's zoom-on-focus, small controls have finger-sized tap areas, and nothing
makes the page scroll sideways.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, Playwright, expect

from harness import ApiClient, LiveServer, User, login, today_utc, wait_for_app
from imaging import encode, halves


@pytest.fixture(scope="module")
def phone_user(live_server: LiveServer, make_user) -> tuple[User, str]:
    user = make_user("phone")
    api = ApiClient.logged_in(live_server.url, user)
    image_id = api.upload_image_id(encode(halves((800, 600)), "JPEG"))
    entry_id = api.create_entry(today_utc(), "Written on the go", image_ids=[image_id])
    api.add_tag(entry_id, "walking")
    api.close()
    return user, entry_id


@pytest.fixture
def phone(
    browser: Browser, playwright: Playwright, live_server: LiveServer, phone_user
) -> Iterator[Page]:
    context = browser.new_context(
        **playwright.devices["iPhone 13"],
        base_url=live_server.url,
        locale="en-US",
        timezone_id="UTC",
    )
    page = context.new_page()
    login(page, phone_user[0])
    yield page
    context.close()


def no_sideways_scrolling(page: Page) -> None:
    width = page.evaluate("() => document.documentElement.scrollWidth")
    assert width <= page.viewport_size["width"], f"page is {width}px wide"


def lands_on(page: Page, locator, *, dx: float = 0, dy: float = 0) -> bool:
    """Whether a tap at the control's edge, offset by (dx, dy), reaches it."""
    box = locator.bounding_box()
    assert box, "control is not visible"
    x = box["x"] + box["width"] / 2 + dx
    y = (box["y"] if dy < 0 else box["y"] + box["height"]) + dy
    return page.evaluate(
        """([el, x, y]) => { const hit = document.elementFromPoint(x, y);
                             return hit === el || el.contains(hit); }""",
        [locator.element_handle(), x, y],
    )


def test_nothing_scrolls_sideways(phone: Page, phone_user) -> None:
    _, entry_id = phone_user
    no_sideways_scrolling(phone)
    phone.locator(f"#entry-{entry_id} [data-lightbox-item]").first.click()
    expect(phone.locator("#image-lightbox")).to_be_visible()
    no_sideways_scrolling(phone)
    phone.keyboard.press("Escape")
    phone.goto("/t")
    wait_for_app(phone)
    no_sideways_scrolling(phone)


def test_entry_actions_show_without_hover(phone: Page, phone_user) -> None:
    _, entry_id = phone_user
    row = phone.locator(f"#entry-{entry_id} .entry-actions-row")
    expect(row).to_be_visible()
    assert row.evaluate("el => getComputedStyle(el).opacity") == "1"


def test_entry_actions_stay_in_line(phone: Page, phone_user) -> None:
    # Enlarging tap areas must not move anything that's drawn.
    _, entry_id = phone_user
    tops = [
        round(box["y"] + box["height"] / 2)
        for box in phone.locator(
            f"#entry-{entry_id} .entry-actions-row .entry-action"
        ).evaluate_all("els => els.map(e => e.getBoundingClientRect().toJSON())")
    ]
    assert len(tops) >= 3 and max(tops) - min(tops) <= 1, tops


def test_fields_do_not_make_ios_zoom(phone: Page) -> None:
    def font_size(selector: str) -> float:
        return phone.locator(selector).evaluate(
            "el => parseFloat(getComputedStyle(el).fontSize)"
        )

    assert font_size("#entry-text") >= 16
    assert font_size("#search-input") >= 16
    phone.goto("/t")
    wait_for_app(phone)
    assert font_size("#tags-view-filter") >= 16


@pytest.mark.parametrize(
    ("name", "locate"),
    [
        ("header icon", lambda page, _: page.get_by_role("button", name="Menu")),
        ("view mode", lambda page, _: page.get_by_role("button", name="Change view")),
        (
            "edit",
            lambda page, e: page.locator(f"#entry-{e}").get_by_role(
                "button", name="Edit entry"
            ),
        ),
        (
            "add traces",
            lambda page, e: page.locator(f"#entry-{e}").get_by_role(
                "button", name="Add traces"
            ),
        ),
        ("attach", lambda page, _: page.get_by_role("button", name="Attach images")),
    ],
)
def test_small_controls_have_finger_sized_tap_areas(
    phone: Page, phone_user, name: str, locate
) -> None:
    _, entry_id = phone_user
    control = locate(phone, entry_id)
    control.scroll_into_view_if_needed()
    expect(control).to_be_visible()
    # A tap a few pixels outside the drawn edge still reaches the control.
    assert lands_on(phone, control, dy=-5), f"{name}: tap above the edge misses"
    assert lands_on(phone, control, dy=5), f"{name}: tap below the edge misses"


def test_the_view_mode_button_is_named_and_tappable(phone: Page) -> None:
    button = phone.get_by_role("button", name="Change view")
    box = button.bounding_box()
    assert box and box["width"] >= 30 and box["height"] >= 30, box


def test_the_scroll_button_waits_in_the_corner(phone: Page) -> None:
    button = phone.locator('scroll-edge-button[data-direction="down"]')
    style = button.evaluate(
        "el => { const s = getComputedStyle(el); return [s.right, s.left, s.transform]; }"
    )
    assert style[0] == "16px" and style[2] == "none", style


# -- the header menu ------------------------------------------------------------


def test_account_icons_fold_into_a_menu(phone: Page) -> None:
    menu = phone.locator("#account-menu")
    for name in ("Need someone to talk to?", "Profile"):
        expect(phone.get_by_role("link", name=name)).to_be_hidden()
    expect(phone.get_by_role("button", name="Logout")).to_be_hidden()
    expect(phone.get_by_role("button", name="Search")).to_be_visible()
    expect(menu).to_be_hidden()

    phone.get_by_role("button", name="Menu").click()

    expect(menu).to_be_visible()
    items = menu.get_by_role("menuitem")
    expect(items).to_have_text(["Need someone to talk to?", "Profile", "Logout"])
    box = items.first.bounding_box()
    assert box and box["height"] >= 44
    phone.keyboard.press("Escape")
    expect(menu).to_be_hidden()


@pytest.mark.parametrize(
    ("item", "tab"), [("Need someone to talk to?", "Support"), ("Profile", "Account")]
)
def test_menu_items_open_the_profile_and_close_the_menu(
    phone: Page, item: str, tab: str
) -> None:
    phone.get_by_role("button", name="Menu").click()
    phone.locator("#account-menu").get_by_role("menuitem", name=item).click()

    expect(phone.locator("#account-menu")).to_be_hidden()
    modal = phone.locator("[data-profile-modal]")
    expect(modal).to_be_visible()
    expect(modal.get_by_role("tab", name=tab)).to_have_attribute(
        "aria-selected", "true"
    )


def test_logging_out_from_the_menu(phone: Page) -> None:
    phone.get_by_role("button", name="Menu").click()
    phone.locator("#account-menu").get_by_role("menuitem", name="Logout").click()

    expect(phone).to_have_url(re.compile(r"/login"))


def test_wide_screens_keep_the_separate_icons(app_page: Page) -> None:
    expect(app_page.get_by_role("button", name="Menu")).to_be_hidden()
    expect(app_page.get_by_role("link", name="Profile")).to_be_visible()
    expect(
        app_page.get_by_role("link", name="Need someone to talk to?")
    ).to_be_visible()
    expect(app_page.get_by_role("button", name="Logout")).to_be_visible()


# -- the traces view -------------------------------------------------------------

SIDEBAR_METRICS = """() => {
  const card = document.querySelector('.tags-view__sidebar-fixed');
  const body = document.querySelector('.tags-view__list-body');
  const c = card.getBoundingClientRect(), b = body.getBoundingClientRect();
  return { gap: c.bottom - b.bottom, bodyHeight: b.height, contentHeight: body.scrollHeight };
}"""


@pytest.mark.parametrize("width", [390, 768])
def test_the_traces_list_card_fits_its_list(
    browser: Browser, live_server: LiveServer, make_user, width: int
) -> None:
    # Narrow layouts cap the list's height; the card must not keep its
    # desktop minimum height and leave an empty band under the list.
    user = make_user("traces")
    api = ApiClient.logged_in(live_server.url, user)
    for i in range(10):
        api.add_tag(api.create_entry(today_utc(), f"Entry {i}"), f"trace-{i}")
    few = make_user("fewtraces")
    few_api = ApiClient.logged_in(live_server.url, few)
    few_api.add_tag(few_api.create_entry(today_utc(), "Just one"), "alone")
    api.close()
    few_api.close()

    for who, path in [(user, "/t/trace-0"), (user, "/t"), (few, "/t/alone")]:
        context = browser.new_context(
            viewport={"width": width, "height": 844}, base_url=live_server.url
        )
        page = context.new_page()
        login(page, who)
        page.goto(path)
        wait_for_app(page)
        expect(page.locator(".tags-view__index-row").first).to_be_visible()
        metrics = page.evaluate(SIDEBAR_METRICS)
        assert metrics["gap"] <= 12, (path, metrics)
        # A short list isn't padded out to a minimum height either.
        assert metrics["bodyHeight"] <= metrics["contentHeight"] + 1, (path, metrics)
        context.close()


def test_the_entry_box_uses_the_screen_width(phone: Page) -> None:
    # Writing room first: the form reaches almost to the edges, and the text
    # area keeps most of the width (it was about 188px of 390 before).
    metrics = phone.evaluate(
        """() => {
          const t = document.querySelector('#entry-text'), s = getComputedStyle(t);
          const f = document.querySelector('#entry-form').getBoundingClientRect();
          const r = t.getBoundingClientRect();
          return { left: f.left, right: innerWidth - f.right,
                   writable: r.width - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight) };
        }"""
    )
    assert metrics["left"] <= 12 and metrics["right"] <= 12, metrics
    assert metrics["writable"] >= 240, metrics
    no_sideways_scrolling(phone)
    send = phone.get_by_role("button", name="Send").bounding_box()
    assert send and send["width"] >= 44 and send["height"] >= 44, send
