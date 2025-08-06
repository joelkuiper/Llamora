let sseListenerBound = false;

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

  // Form behavior
  form.addEventListener("htmx:afterRequest", () => setFormEnabled(false));
  form.addEventListener("htmx:configRequest", (event) => {
    if (!textarea.value.trim()) {
      event.preventDefault();
      textarea.focus();
    }
  });

  // Error handling
  errors?.addEventListener("htmx:afterSwap", () => {
    requestAnimationFrame(() => {
      if (document.querySelector("#errors .error-box")) {
        setFormEnabled(true);
      }
    });
  });

  // Auto-scroll after chat box updates
  chatBox.addEventListener("htmx:afterSwap", scrollToBottom);

  // Initial scroll and focus
  scrollToBottom();
  textarea.focus();
}


/**
 * Sets up scroll and SSE behavior for the chat panel.
 */
function setupScrollHandler(setFormEnabled, containerSelector = ".chat-panel") {
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

  // Bind SSE listener once per page load
  if (!sseListenerBound) {
    document.body.addEventListener("htmx:sseMessage", (evt) => {
      if (evt.detail.type === "done") {
        setFormEnabled(true);
      } else if (evt.detail.type === "message") {
        scrollToBottom();
      }
    });
    sseListenerBound = true;
  }

  // Initial scroll
  container.scrollTop = container.scrollHeight;
  return scrollToBottom;
}
