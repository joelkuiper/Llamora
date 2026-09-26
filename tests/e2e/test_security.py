"""Untrusted markup, from the user or the model, must never execute.

Entries are rendered to HTML on the server (markdown + bleach); model replies
are also rendered in the browser while streaming (markdown-it + DOMPurify).
Both paths are exercised, live and after a reload.
"""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect

from fake_llm import FakeLLM
from harness import marker, wait_for_app, write_entry

# Every payload flips window.__xss if it ever runs.
PAYLOADS = (
    '<img src="x" onerror="window.__xss=1">',
    "<script>window.__xss=1</script>",
    '<svg onload="window.__xss=1"><circle r="1"/></svg>',
    '<iframe src="javascript:window.__xss=1"></iframe>',
    '<a href="javascript:window.__xss=1">html link</a>',
    "[markdown link](javascript:window.__xss=1)",
    '<div style="background:url(javascript:window.__xss=1)">styled</div>',
)
XSS = "\n\n".join(PAYLOADS)

# Data holders like <script type="text/plain" class="entry-raw"> never execute.
DANGEROUS = (
    "script:not([type='text/plain']), iframe, object, embed, "
    "[onerror], [onload], [onclick], "
    "[href^='javascript' i], [src^='javascript' i]"
)


def expect_inert(page: Page, container: Locator) -> None:
    expect(container).to_be_visible()
    expect(container.locator(DANGEROUS)).to_have_count(0)
    # Give any stray handler (image error, svg load) a chance to fire.
    page.wait_for_timeout(300)
    assert page.evaluate("window.__xss") is None, "injected markup executed"


def click_links(container: Locator) -> None:
    """Clicking a neutralised javascript: link must do nothing either."""
    for link in container.locator("a").all():
        link.click(modifiers=["Alt"], no_wait_after=True)


def test_entry_markup_is_neutralised(app_page: Page) -> None:
    token = marker("inert")
    dialogs: list[str] = []
    app_page.on(
        "dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss())
    )

    textarea = app_page.locator("#entry-text")
    expect(textarea).to_be_enabled()
    textarea.fill(f"{token}\n\n{XSS}")
    textarea.press("Enter")

    entry = app_page.locator("#entries .entry.user", has_text=token)
    expect_inert(app_page, entry)
    click_links(entry)
    expect_inert(app_page, entry)

    app_page.reload()
    wait_for_app(app_page)
    expect_inert(app_page, app_page.locator("#entries .entry.user", has_text=token))
    assert dialogs == []


def test_streamed_model_reply_is_neutralised(app_page: Page, fake_llm: FakeLLM) -> None:
    token = marker("reply")
    fake_llm.reply = f"{token}\n\n{XSS}"
    entry = write_entry(app_page, f"Tell me something {marker()}")
    entry_id = entry.get_attribute("data-entry-id")

    entry.get_by_role("button", name="Respond").click()
    responses = app_page.locator(f"#entry-responses-{entry_id}")
    expect(responses).to_contain_text(token)
    wait_for_app(app_page)
    expect_inert(app_page, responses)

    app_page.reload()
    wait_for_app(app_page)
    expect_inert(app_page, app_page.locator(f"#entry-responses-{entry_id}"))
