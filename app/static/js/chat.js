let currentSSEListener = null;

/**
 * Convert Markdown content within a container to HTML.
 *
 * Rendering is skipped after the first pass to avoid re-processing already
 * rendered content. Setting `force` to `true` bypasses this guard, allowing
 * repeated rendering of the same element. This is useful during server-sent
 * event (SSE) streaming where new Markdown chunks arrive incrementally and
 * must be re-rendered on each update.
 *
 * @param {HTMLElement} root - The element whose Markdown should be rendered.
 *   If it contains an `[sse-swap]` child, that child is rendered instead.
 * @param {boolean} [force=false] - When `true`, bypasses the `data-md-rendered`
 *   check to force re-rendering.
 */
export function renderMarkdown(root, force = false) {
  const target = root?.querySelector('[sse-swap]') || root;
  if (!target) return;
  if (!force && target.dataset.mdRendered) return;

  const text = target.textContent;
  if (window.marked) {
    target.innerHTML = window.marked.parse(text);
  } else {
    target.textContent = text;
  }
  target.dataset.mdRendered = 'true';
}

export function initChatUI(root = document) {
  const form = root.querySelector("#chat-form");
  const textarea = form?.querySelector("textarea");
  const button = form?.querySelector("button");
  const chatBox = root.querySelector("#chat-box");
  const errors = document.getElementById("errors");

  if (!form || !textarea || !button || !chatBox) return;

  const setFormEnabled = (enabled) => {
    textarea.disabled = !enabled;
    button.disabled = !enabled;
    if (enabled) textarea.focus();
  };

  const scrollToBottom = setupScrollHandler(setFormEnabled);

  form.addEventListener("htmx:afterRequest", () => setFormEnabled(false));

  form.addEventListener("htmx:configRequest", (event) => {
    if (!textarea.value.trim()) {
      event.preventDefault();
      textarea.focus();
    }
  });

  errors?.addEventListener("htmx:afterSwap", () => {
    requestAnimationFrame(() => {
      if (document.querySelector("#errors .error-box")) {
        setFormEnabled(true);
      }
    });
  });

  chatBox.addEventListener("htmx:afterSwap", scrollToBottom);

  scrollToBottom();
  textarea.focus();
}

function setupScrollHandler(setFormEnabled, containerSelector = "#chatbox-wrapper") {
  const container = document.querySelector(containerSelector);
  if (!container) return () => {};

  let autoScrollEnabled = true;
  let lastScrollTop = container.scrollTop;
  const SCROLL_THRESHOLD = 10;

  const scrollToBottom = () => {
    if (autoScrollEnabled) {
      container.scrollTo({
        top: container.scrollHeight,
        behavior: "smooth",
      });
    }
  };

  const isUserNearBottom = () => {
    const distanceFromBottom =
          container.scrollHeight - container.clientHeight - container.scrollTop;
    return distanceFromBottom < SCROLL_THRESHOLD;
  };

  const updateScrollState = (currentTop) => {
    if (currentTop < lastScrollTop - 2) {
      autoScrollEnabled = false;
    } else if (isUserNearBottom()) {
      autoScrollEnabled = true;
    }
    lastScrollTop = currentTop;
  };

  container.addEventListener("scroll", () => {
    updateScrollState(container.scrollTop);
  });

  container.addEventListener("wheel", (e) => {
    if (e.deltaY < 0) autoScrollEnabled = false;
  }, { passive: true });

  container.addEventListener("touchmove", () => {
    if (container.scrollTop < lastScrollTop) autoScrollEnabled = false;
    lastScrollTop = container.scrollTop;
  }, { passive: true });

  // Remove previous SSE listener if any
  if (currentSSEListener) {
    document.body.removeEventListener("htmx:sseMessage", currentSSEListener);
  }

  // Add new listener with current setFormEnabled closure
  currentSSEListener = (evt) => {
    if (evt.detail.type === "done") {
      const container = evt.target.closest('.bot-stream');
      if (container) {
        renderMarkdown(container, true);
        container.classList.remove('bot-stream');
        container.classList.add('bot');
      }
      setFormEnabled(true);
    } else if (evt.detail.type === "message") {
      const container = evt.target.closest('.bot-stream');
      if (container) {
        renderMarkdown(container, true);
      }
      scrollToBottom();
    }
  };
  document.body.addEventListener("htmx:sseMessage", currentSSEListener);

  container.scrollTop = container.scrollHeight;
  return scrollToBottom;
}
