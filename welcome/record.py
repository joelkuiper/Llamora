"""Record the welcome page's hero video from the demo journal, light and dark.

Run the app with the demo database on http://localhost:5000 (see the README's
"Try the Demo"), then:

    uv run python welcome/record.py            # both themes
    uv run python welcome/record.py dark       # one

A walk through the app: write an entry on today's page, ask for a response and
watch it stream in, keep two of the suggested traces, go to the 25th through
the calendar, hover a few days on the traces heatmap, then search and open a
result. It writes to the (ephemeral) demo journal, and
deletes its entry again once the recording has stopped.

Frames come from Chrome's screencast (sharper than Playwright's own video),
then ffmpeg turns them into a constant 30 fps loop that fades in and out of
the page background: hero-{theme}.mp4 (H.264) and hero-{theme}.webm (VP9).
Needs ffmpeg with libx264 and libvpx-vp9.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).parent / "img"
BASE = "http://localhost:5000"
VIEWPORT = {"width": 1440, "height": 900}
SIZE = (1920, 1200)  # encoded size; frames are captured at 2x and scaled down
FPS = 30
FADE = 0.6
BACKGROUND = {"light": "0xfafafa", "dark": "0x0d0f12"}

# A drawn pointer (headless Chrome has none), and nothing that only a real
# hover would leave behind.
INIT = """
(() => {
  const install = () => {
    if (document.getElementById('demo-cursor')) return;
    const style = document.createElement('style');
    style.textContent = `
      scroll-edge-button, .tooltip, [role="tooltip"], .tooltip-root { display: none !important; }
      * { caret-color: transparent; }
      #demo-cursor { position: fixed; left: 0; top: 0; z-index: 2147483647; pointer-events: none;
        width: 22px; height: 22px; transform: translate(-100px, -100px);
        filter: drop-shadow(0 1px 2px rgba(0,0,0,.35)); }
      #demo-cursor svg { transition: transform 140ms ease; transform-origin: 3px 3px; }
      #demo-cursor.is-down svg { transform: scale(.82); }`;
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.innerHTML = `<svg width="22" height="22" viewBox="0 0 22 22">
      <path d="M3 2 L3 18 L7.5 13.8 L10.6 20.2 L13.2 19 L10.2 12.8 L16.4 12.8 Z"
            fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    document.head.append(style);
    document.body.append(cursor);
    addEventListener('mousemove', e => {
      cursor.style.transform = `translate(${e.clientX - 3}px, ${e.clientY - 2}px)`;
    }, true);
    addEventListener('mousedown', () => cursor.classList.add('is-down'), true);
    addEventListener('mouseup', () => cursor.classList.remove('is-down'), true);
  };
  if (document.body) install(); else addEventListener('DOMContentLoaded', install);
})();
"""


class Pointer:
    """Unhurried mouse movement, eased, so the drawn cursor glides."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.x, self.y = VIEWPORT["width"] * 0.6, VIEWPORT["height"] * 0.75

    def move_to(self, x: float, y: float, ms: int = 700) -> None:
        # Paced by the clock, not by step count: each step costs more than it waits.
        x0, y0 = self.x, self.y
        start = time.monotonic()
        while True:
            t = min((time.monotonic() - start) * 1000 / ms, 1.0)
            e = t * t * (3 - 2 * t)  # smoothstep
            self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            if t >= 1:
                break
            self.page.wait_for_timeout(8)
        self.x, self.y = x, y

    def to(self, selector: str, ms: int = 700, dx: float = 0, dy: float = 0) -> None:
        box = self.page.locator(selector).first.bounding_box()
        assert box, f"{selector} is not visible"
        self.move_to(
            box["x"] + box["width"] / 2 + dx, box["y"] + box["height"] / 2 + dy, ms
        )

    def to_locator(self, locator, ms: int = 700) -> None:
        box = locator.bounding_box()
        assert box, "target is not visible"
        self.move_to(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, ms)

    def click_locator(self, locator, ms: int = 700) -> None:
        self.to_locator(locator, ms)
        self.click()

    def click(self, selector: str | None = None, ms: int = 700) -> None:
        if selector:
            self.to(selector, ms)
        self.page.wait_for_timeout(150)
        self.page.mouse.down()
        self.page.wait_for_timeout(120)
        self.page.mouse.up()


def pause(page: Page, seconds: float) -> None:
    # Not time.sleep: the sync API only handles screencast frames during its own calls.
    page.wait_for_timeout(seconds * 1000)


def scroll_diary(page: Page, pixels: int, ms: int = 1600) -> None:
    """Scroll the diary's own scroller by `pixels`, eased (it isn't the window)."""
    page.evaluate(
        """([pixels, ms]) => {
          const el = [...document.querySelectorAll('*')]
            .filter(e => e.scrollHeight > e.clientHeight + 40 && /auto|scroll/.test(getComputedStyle(e).overflowY))
            .sort((a, b) => b.scrollHeight - a.scrollHeight)[0] || document.scrollingElement;
          const from = el.scrollTop, start = performance.now();
          const step = now => {
            const t = Math.min((now - start) / ms, 1);
            el.scrollTop = from + pixels * t * t * (3 - 2 * t);
            if (t < 1) requestAnimationFrame(step);
          };
          requestAnimationFrame(step);
        }""",
        [pixels, ms],
    )
    page.wait_for_timeout(ms + 100)


def login(page: Page) -> None:
    page.goto("/login")
    page.fill('input[name="username"]', "demo_user")
    page.fill('input[name="password"]', "demo_user_test_password12345!")
    page.click('button:has-text("Login")')
    page.wait_for_url("**/d/**", timeout=30000)


ENTRY = (
    "Walked along the river after dinner. The water was so still it held the "
    "whole sky, and for once my head was quiet too."
)


def streaming_done(page: Page, entry_id: str) -> None:
    """Wait for the model's reply to this entry to finish streaming."""
    page.wait_for_function(
        """id => {
          const box = document.getElementById(`entry-responses-${id}`);
          const stream = box?.querySelector('response-stream');
          return stream && stream.dataset.streaming === 'false' && box.innerText.trim().length > 40;
        }""",
        arg=entry_id,
        timeout=240000,
        polling=250,
    )


def heatmap_days(page: Page):
    """A few recent days with entries, spread over the heatmap's last months."""
    days = page.locator("a.activity-heatmap__cell:not(.is-empty-day)")
    count = days.count()
    return [days.nth(max(i, 0)) for i in (count - 16, count - 9, count - 3)]


def heatmap_summary(page: Page) -> None:
    """Wait for the hovered day's preview, summary included."""
    page.wait_for_function(
        """() => {
          const tip = document.querySelector('.heatmap-day-tooltip.is-visible');
          const text = tip?.querySelector('.heatmap-day-tooltip__summary')?.textContent || '';
          return text.trim().length > 20;
        }""",
        timeout=120000,
        polling=200,
    )


CALENDAR_DAY = '#calendar-popover td[data-date="2026-09-25"]'


def calendar_summary(page: Page) -> None:
    """Wait for the hovered calendar day's summary."""
    page.wait_for_function(
        """() => {
          const tip = document.querySelector('.calendar-day-tooltip.is-visible:not(.heatmap-day-tooltip)');
          return tip && tip.textContent.trim().length > 40;
        }""",
        timeout=120000,
        polling=200,
    )


def warm_up(page: Page) -> None:
    """Have the day summaries it hovers generated (and cached) before filming."""
    page.goto("/d/today")
    page.wait_for_timeout(1500)
    page.locator("#calendar-btn").click()
    page.locator(CALENDAR_DAY).hover()
    calendar_summary(page)
    page.keyboard.press("Escape")
    page.goto("/t")
    page.wait_for_timeout(2000)
    for day in heatmap_days(page):
        day.hover()
        heatmap_summary(page)
    page.mouse.move(5, 5)


def storyboard(page: Page, pointer: Pointer) -> str:
    """The walk through the app; returns the id of the entry it wrote."""
    pause(page, 1.0)

    # Write an entry.
    pointer.click("#entry-text", ms=900)
    pause(page, 0.3)
    page.keyboard.type(ENTRY, delay=38)
    pause(page, 0.5)
    pointer.click("#send-btn", ms=500)
    entry = page.locator("#entries .entry.user", has_text="Walked along the river").last
    entry.wait_for(timeout=30000)
    entry_id = entry.get_attribute("data-entry-id")
    entry = page.locator(f"#entry-{entry_id}")
    pause(page, 0.9)

    # Ask for a response and watch it arrive; the pointer steps aside to the
    # left, well away from the reply (and its stop control).
    pointer.click(f"#entry-{entry_id} [aria-label='Respond']", ms=800)
    pointer.move_to(VIEWPORT["width"] * 0.12, VIEWPORT["height"] * 0.6, ms=1100)
    streaming_done(page, entry_id)
    pause(page, 1.2)

    # Keep a couple of the model's suggested traces.
    pointer.click(f"#entry-{entry_id} [aria-label='Add traces']", ms=900)
    suggestions = page.locator(
        "#tag-popover-global .tag-suggestion:not(.tag-suggestion--skeleton)"
    )
    suggestions.first.wait_for(timeout=120000)
    pause(page, 0.9)
    for _ in range(2):
        chip = suggestions.nth(1) if suggestions.count() > 1 else suggestions.first
        pointer.click_locator(chip, ms=650)
        pause(page, 0.8)
    page.keyboard.press("Escape")
    pause(page, 0.8)

    # The calendar: hover the 25th for its summary, then go there.
    pointer.click("#calendar-btn", ms=900)
    pause(page, 0.7)
    pointer.to(CALENDAR_DAY, ms=800)
    calendar_summary(page)
    pause(page, 1.8)
    pointer.click()
    page.wait_for_url("**/d/2026-09-25")
    pause(page, 1.4)
    scroll_diary(page, 260, ms=1200)
    pause(page, 0.6)

    # The traces view, and a few days on the heatmap.
    pointer.click('[aria-label="Change view"]', ms=900)
    pause(page, 0.5)
    pointer.click('.view-mode-option[data-view-mode="tags"]', ms=450)
    page.wait_for_url("**/t**")
    pause(page, 1.1)
    for day in heatmap_days(page):
        pointer.to_locator(day, ms=700)
        heatmap_summary(page)
        pause(page, 1.5)

    # Search.
    pointer.click("#search-input", ms=900)
    pause(page, 0.3)
    page.keyboard.type("solitude", delay=90)
    pause(page, 0.3)
    page.keyboard.press("Enter")
    results = page.locator(".search-results-overlay li a")
    results.first.wait_for(timeout=30000)
    pause(page, 1.0)

    # Open a result: its day comes in, with the entry in view.
    pointer.click_locator(results.nth(1), ms=900)
    page.wait_for_url("**/d/**")
    pause(page, 1.0)
    pointer.move_to(VIEWPORT["width"] * 0.85, VIEWPORT["height"] * 0.7, ms=900)
    pause(page, 1.8)
    return entry_id


def clean_up(page: Page, entry_id: str) -> None:
    """Delete the recorded entry (and its reply), off camera."""
    page.goto("/d/today")
    entry = page.locator(f"#entry-{entry_id}")
    entry.wait_for(timeout=30000)
    entry.get_by_role("button", name="Delete entry").click()
    page.locator("[role=dialog], dialog").get_by_role("button", name="Delete").click()
    entry.wait_for(state="detached", timeout=30000)


def record(p, scheme: str, frames_dir: Path) -> tuple[list[tuple[Path, float]], float]:
    browser = p.chromium.launch()
    context = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=2,
        base_url=BASE,
        timezone_id="Europe/Amsterdam",
        color_scheme=scheme,
    )
    context.add_init_script(INIT)
    page = context.new_page()
    login(page)
    warm_up(page)
    page.goto("/d/today")
    page.wait_for_timeout(2500)
    pointer = Pointer(page)
    page.mouse.move(pointer.x, pointer.y)

    frames: list[tuple[Path, float]] = []
    cdp = context.new_cdp_session(page)

    def on_frame(event) -> None:
        path = frames_dir / f"f{len(frames):05d}.jpg"
        path.write_bytes(base64.b64decode(event["data"]))
        frames.append((path, event["metadata"]["timestamp"]))
        cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    cdp.on("Page.screencastFrame", on_frame)
    cdp.send(
        "Page.startScreencast",
        {"format": "jpeg", "quality": 92, "maxWidth": SIZE[0], "maxHeight": SIZE[1]},
    )
    entry_id = storyboard(page, pointer)
    end = time.time()
    cdp.send("Page.stopScreencast")
    clean_up(page, entry_id)
    browser.close()
    return frames, end


def encode(
    frames: list[tuple[Path, float]], end: float, scheme: str, work: Path
) -> None:
    assert len(frames) > 10, "no frames captured"
    listing = work / "frames.txt"
    lines = []
    for (path, ts), (_, next_ts) in zip(frames, frames[1:] + [(None, end)]):
        lines += [f"file '{path}'", f"duration {max(next_ts - ts, 0.001):.4f}"]
    lines.append(
        f"file '{frames[-1][0]}'"
    )  # the concat demuxer drops the last duration otherwise
    listing.write_text("\n".join(lines) + "\n")
    total = end - frames[0][1]
    color = BACKGROUND[scheme]
    video = (
        f"fps={FPS},scale={SIZE[0]}:{SIZE[1]}:flags=lanczos,"
        f"fade=t=in:st=0:d={FADE}:color={color},"
        f"fade=t=out:st={total - FADE:.3f}:d={FADE}:color={color},format=yuv420p"
    )
    common = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-vf",
        video,
        "-an",
    ]
    mp4, webm = OUT / f"hero-{scheme}.mp4", OUT / f"hero-{scheme}.webm"
    subprocess.run(
        [
            *common,
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "24",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        check=True,
    )
    subprocess.run(
        [
            *common,
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "0",
            "-crf",
            "36",
            "-row-mt",
            "1",
            "-deadline",
            "good",
            str(webm),
        ],
        check=True,
    )
    for f in (mp4, webm):
        print(
            f"{f.name}: {f.stat().st_size / 1e6:.1f} MB, {total:.1f}s, {len(frames)} frames"
        )


def main() -> None:
    schemes = sys.argv[1:] or ["light", "dark"]
    with sync_playwright() as p:
        for scheme in schemes:
            work = Path(tempfile.mkdtemp(prefix=f"llamora-hero-{scheme}-"))
            try:
                frames, end = record(p, scheme, work)
                encode(frames, end, scheme, work)
            finally:
                shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
