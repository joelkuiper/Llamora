"""Capture the welcome page's screenshots from the demo journal, light and dark.

Run the app with the demo database on http://localhost:5000 (see the README's
"Try the Demo"), then:

    uv run python welcome/capture.py                 # everything
    uv run python welcome/capture.py page markdown   # only some: page, desktop, markdown, mobile

The shots assume the demo's dates: "today" (27 Sep 2026) has the eclipse photos,
25 Sep has the entries used for traces and the calendar, 15 Sep the Markdown entry.
Desktop shots share one 1120x700 frame at 2x; the traces view and the hero are
wider, but keep the same 16:10 shape.
"""

import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "img"
BASE = "http://localhost:5000"
HIDE = """
scroll-edge-button, .tooltip, [role="tooltip"], .tooltip-root { display: none !important; }
* { caret-color: transparent !important; }
"""
UNION = """(selectors) => {
  const rects = selectors.flatMap(s => [...document.querySelectorAll(s)]).map(e => e.getBoundingClientRect()).filter(r => r.width && r.height);
  const l = Math.min(...rects.map(r => r.left)), t = Math.min(...rects.map(r => r.top));
  const r = Math.max(...rects.map(r => r.right)), b = Math.max(...rects.map(r => r.bottom));
  return {x: l, y: t, width: r - l, height: b - t};
}"""


def save(png: bytes, name: str) -> None:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.save(OUT / f"{name}.webp", "WEBP", quality=86, method=6)
    print(name, image.size)


def login(page):
    page.goto("/login")
    page.fill('input[name="username"]', "demo_user")
    page.fill('input[name="password"]', "demo_user_test_password12345!")
    page.click('button:has-text("Login")')
    page.wait_for_url("**/d/**", timeout=30000)


def settle(page, ms=1500):
    page.add_style_tag(content=HIDE)
    page.mouse.move(2, 890)
    page.wait_for_timeout(ms)


FRAME_W, FRAME_H = 1120, 700


def frame(page, selectors, name):
    """Every desktop shot: the same 1120x700 window onto the app, centred on its subject."""
    box = page.evaluate(UNION, selectors)
    vw, vh = page.viewport_size["width"], page.viewport_size["height"]
    if box["width"] > FRAME_W - 32 or box["height"] > FRAME_H - 32:
        print(
            f"  note: {name} subject {round(box['width'])}x{round(box['height'])} exceeds frame"
        )
    cy = box["y"] + box["height"] / 2
    y = cy - FRAME_H / 2 if box["height"] <= FRAME_H - 32 else box["y"] - 16
    x = (vw - FRAME_W) / 2  # every frame sits on the centred column...
    x = min(x, box["x"] - 16)  # ...unless its subject reaches past it
    x = min(max(x, 0), vw - FRAME_W)
    y = min(max(y, 0), vh - FRAME_H)
    header = page.evaluate(
        "() => document.querySelector('header')?.getBoundingClientRect().bottom || 0"
    )
    if 0 < y < header:  # never slice the header: all of it, or none
        y = 0 if box["y"] < header else header
    y = min(y, vh - FRAME_H)
    save(
        page.screenshot(clip={"x": x, "y": y, "width": FRAME_W, "height": FRAME_H}),
        name,
    )


def desktop(p, scheme):
    ctx = p.chromium.launch().new_context(
        viewport={"width": 1280, "height": 900},
        device_scale_factor=2,
        base_url=BASE,
        timezone_id="Europe/Amsterdam",
        color_scheme=scheme,
    )
    page = ctx.new_page()
    login(page)
    t = scheme

    # Today's photo entry (for the lightbox).
    page.goto("/d/today")
    settle(page, 2500)
    photo = page.locator(
        "#entries .entry.user", has=page.locator(".entry-images")
    ).first.get_attribute("data-entry-id")

    # The lightbox: a window exactly the frame's size, so the overlay's edges and buttons fit.
    page.set_viewport_size({"width": FRAME_W, "height": FRAME_H})
    page.goto("/d/today")
    settle(page, 2000)
    page.locator(f"#entry-{photo} [data-lightbox-item]").first.click()
    page.wait_for_timeout(1500)
    save(page.screenshot(), f"lightbox-{t}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto("/d/today")
    settle(page, 1500)

    # Calendar popover.
    page.locator("#calendar-btn").click()
    page.wait_for_timeout(900)
    # Hover a day so its summary appears (it may be generated on first hover).
    page.hover('#calendar-popover td[data-date="2026-09-25"]')
    page.locator(".calendar-day-tooltip.is-visible").wait_for(timeout=60000)
    page.wait_for_function(
        "() => { const t = document.querySelector('.calendar-day-tooltip');"
        " return t && t.textContent.trim().length > 40 && !t.querySelector('[class*=spinner]'); }",
        timeout=60000,
    )
    page.wait_for_timeout(600)
    frame(page, ["#calendar-popover", ".calendar-day-tooltip"], f"calendar-{t}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    # Search.
    page.goto("/d/today")
    settle(page)  # fresh page: nothing left focused
    page.locator("#search-input").fill("solitude")
    page.locator("#search-input").press("Enter")
    page.wait_for_timeout(3500)
    frame(page, ["#search-input", ".search-results-overlay"], f"search-{t}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Trace suggestions on an entry (25 Sep, second entry).
    page.goto("/d/2026-09-25")
    settle(page)
    entry = page.locator("#entries .entry.user").nth(1)
    entry.scroll_into_view_if_needed()
    entry.get_by_role("button", name="Add traces").click()
    page.wait_for_timeout(6000)
    frame(
        page,
        [f"#entry-{entry.get_attribute('data-entry-id')}", "#tag-popover-global"],
        f"suggest-{t}",
    )
    page.keyboard.press("Escape")

    # Traces view.
    # Wide enough for the heatmap to fit on one row; clipped to the layout, 16:10.
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.goto("/t")
    settle(page, 2500)
    box = page.evaluate(
        UNION, [".tags-view__sidebar-fixed", ".tags-view__detail-inner"]
    )
    x = box["x"] - 16
    w = box["width"] + 32
    h = w * FRAME_H / FRAME_W
    save(page.screenshot(clip={"x": x, "y": 0, "width": w, "height": h}), f"traces-{t}")
    ctx.close()


def whole_page(p, scheme):
    """The hero: the whole app window on a daily page, 16:10."""
    ctx = p.chromium.launch().new_context(
        viewport={"width": 1440, "height": 900},
        device_scale_factor=2,
        base_url=BASE,
        timezone_id="Europe/Amsterdam",
        color_scheme=scheme,
    )
    page = ctx.new_page()
    login(page)
    page.goto("/d/today")
    settle(page, 2500)
    save(page.screenshot(), f"page-{scheme}")
    ctx.close()


def markdown(p, scheme):
    """An entry written in Markdown: a heading, a list and a quote (15 Sep)."""
    ctx = p.chromium.launch().new_context(
        viewport={"width": 1280, "height": 900},
        device_scale_factor=2,
        base_url=BASE,
        timezone_id="Europe/Amsterdam",
        color_scheme=scheme,
    )
    page = ctx.new_page()
    login(page)
    page.goto("/d/2026-09-15")
    settle(page, 2000)
    entry = page.locator("#entries .entry.user", has=page.locator("h2")).first
    # Put the heading well below the header, in the diary's own scroller.
    entry.locator("h2").first.evaluate("""el => {
      let s = el.parentElement;
      while (s && !(s.scrollHeight > s.clientHeight + 40 && /auto|scroll/.test(getComputedStyle(s).overflowY))) s = s.parentElement;
      s = s || document.scrollingElement;
      s.scrollTop += el.getBoundingClientRect().top - 170;
    }""")
    page.wait_for_timeout(600)
    eid = entry.get_attribute("id")
    frame(
        page, [f"#{eid} h2", f"#{eid} ul", f"#{eid} blockquote"], f"markdown-{scheme}"
    )
    ctx.close()


def mobile(p, scheme):
    ctx = p.chromium.launch().new_context(
        **p.devices["iPhone 13"],
        base_url=BASE,
        timezone_id="Europe/Amsterdam",
        color_scheme=scheme,
    )
    page = ctx.new_page()
    login(page)
    page.goto("/d/2026-09-25")
    settle(page)
    save(page.screenshot(), f"mobile-{scheme}")
    ctx.close()


ONLY = sys.argv[1:]  # e.g. "page" to retake just the hero

with sync_playwright() as p:
    for scheme in ("light", "dark"):
        if not ONLY or "page" in ONLY:
            whole_page(p, scheme)
        if not ONLY or "desktop" in ONLY:
            desktop(p, scheme)
        if not ONLY or "markdown" in ONLY:
            markdown(p, scheme)
        if not ONLY or "mobile" in ONLY:
            mobile(p, scheme)
