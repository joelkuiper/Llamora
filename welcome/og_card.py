"""Render the link-preview card (og:image) from og-card.html.

    uv run python welcome/og_card.py

Writes img/og-card.jpg at 1200x630, the size X, Slack, Discord and WhatsApp
expect; JPEG, because not every scraper reads WebP, and small, because
WhatsApp drops large preview images.
"""

import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
WIDTH, HEIGHT = 1200, 630


def main() -> None:
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(
            viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2
        )
        page.goto((HERE / "og-card.html").resolve().as_uri())
        page.wait_for_load_state("networkidle")
        png = page.screenshot()
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    out = HERE / "img" / "og-card.jpg"
    image.save(out, "JPEG", quality=88, optimize=True, progressive=True)
    print(
        f"{out.relative_to(HERE)}: {image.size[0]}x{image.size[1]}, {out.stat().st_size // 1024} KB"
    )


if __name__ == "__main__":
    main()
