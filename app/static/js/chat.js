let currentSSEListener = null;

function renderMarkdown(text) {
  const rawHtml = marked.parse(text, { gfm: true, breaks: true });

  return DOMPurify.sanitize(rawHtml);
}

function renderAllMarkdown(root) {
  root.querySelectorAll('.bot').forEach(el => {
    if (el.dataset.rendered !== 'true') {
      let text = el.textContent;
      renderMarkdownInElement(el, text);
    }
  });
}

function renderMarkdownInElement(el, text) {
  if (!el) return;
  const src = text !== undefined ? text : el.textContent || "";


  const markdownHtml = renderMarkdown(src);

  const wrapper = document.createElement("div");
  wrapper.className = "markdown-body";
  wrapper.innerHTML = markdownHtml;

  el.innerHTML = "";
  el.appendChild(wrapper);

  el.dataset.rendered = "true";
}


export function initChatUI(root = document) {
  const form = root.querySelector("#chat-form");
  const textarea = form?.querySelector("textarea");
  const button = form?.querySelector("button");
  const chat = root.querySelector("#chat");
  const errors = document.getElementById("errors");

  if (!form || !textarea || !button || !chat) return;

  const sessionId = chat.dataset.sessionId;
  const draftKey = `chat-draft-${sessionId}`;

  textarea.value = sessionStorage.getItem(draftKey) || "";

  const setFormEnabled = (enabled) => {
    textarea.disabled = !enabled;
    button.disabled = !enabled;
    if (enabled) textarea.focus();
  };

  const scrollToBottom = setupScrollHandler(setFormEnabled);

  form.addEventListener("htmx:afterRequest", () => {
    setFormEnabled(false);
    sessionStorage.removeItem(draftKey);
  });

  form.addEventListener("htmx:configRequest", (event) => {
    if (!textarea.value.trim()) {
      event.preventDefault();
      textarea.focus();
    }
  });

  textarea.addEventListener("input", () => {
    sessionStorage.setItem(draftKey, textarea.value);
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
  // If a bot response is currently streaming, keep the form disabled
  if (chat.querySelector("#typing-indicator")) {
    setFormEnabled(false);
  } else {
    textarea.focus();
  }
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

  const sseRenders = new WeakMap();

  function scheduleRender(container, fn) {
    const prev = sseRenders.get(container);
    if (prev) cancelAnimationFrame(prev);
    const id = requestAnimationFrame(() => {
      sseRenders.delete(container);
      fn();
    });
    sseRenders.set(container, id);
  }


  currentSSEListener = (evt) => {
    const { type } = evt.detail;
    const wrap = evt.target.closest('.bot-stream');
    if (!wrap) return;

    const sink = wrap.querySelector('.raw-response');
    const contentDiv = wrap.querySelector('.markdown-body');
    if (!sink || !contentDiv) return;

    const renderNow = () => {
      let text = (sink.textContent || "").replace(/\[newline\]/g, "\n");

      const typing = wrap.querySelector("#typing-indicator");
      contentDiv.innerHTML = renderMarkdown(text);

      if (typing) {
        // Move it into the last paragraph/inline node if possible
        const lastChild = contentDiv.lastElementChild;

        if (lastChild && lastChild.tagName.match(/^(P|LI|SPAN|STRONG|EM|CODE)$/)) {
          lastChild.appendChild(typing);
        } else {
          contentDiv.appendChild(typing); // fallback
        }
      }

    };


    if (type === "message") {
      scheduleRender(wrap, () => { renderNow(); scrollToBottom(); });
    } else if (type === "error" || type === "done") {
      const rid = sseRenders.get(wrap);
      if (rid) { cancelAnimationFrame(rid); sseRenders.delete(wrap); }
      renderNow();

      wrap.querySelector("#typing-indicator")?.remove();
      if (type === "done") setFormEnabled(true);
      scrollToBottom();
    }
  };

  document.body.addEventListener("htmx:sseMessage", currentSSEListener);

  container.scrollTop = container.scrollHeight;
  return scrollToBottom;
}
