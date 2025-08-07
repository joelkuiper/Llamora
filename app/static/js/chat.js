let currentSSEListener = null;

// Set options
marked.use({
  gfm: true
});

function renderMarkdown(text) {
  return marked.parse(text, {"breaks": true});
}

function renderMarkdownInElement(el, text) {
  if (!el) return;
  const src = text !== undefined ? text : el.textContent;
  el.innerHTML = renderMarkdown(src);
  el.dataset.rendered = 'true';
}

function renderAllMarkdown(root) {
  root.querySelectorAll('.user, .bot').forEach(el => {
    if (el.dataset.rendered !== 'true') {
      renderMarkdownInElement(el);
    }
  });
}

export function initChatUI(root = document) {
  const form = root.querySelector("#chat-form");
  const textarea = form?.querySelector("textarea");
  const button = form?.querySelector("button");
  const chat = root.querySelector("#chat");
  const errors = document.getElementById("errors");

  if (!form || !textarea || !button || !chat) return;

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

  chat.addEventListener("htmx:afterSwap", () => {
    scrollToBottom();
    renderAllMarkdown(chat);
  });

  renderAllMarkdown(chat);

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

  currentSSEListener = (evt) => {
    if (evt.detail.type === "message") {
      scrollToBottom();
    } else if (evt.detail.type === "done") {
      setFormEnabled(true);
    }
  };
  document.body.addEventListener("htmx:sseMessage", currentSSEListener);

  container.scrollTop = container.scrollHeight;
  return scrollToBottom;
}
