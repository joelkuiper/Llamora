import { renderMarkdown, renderAllMarkdown } from "./markdown.js";
import { positionTypingIndicator } from "./typing-indicator.js";
import { initTagPopovers } from "./meta-chips.js";

let currentSSEListener = null;
let currentStreamMsgId = null;


const TYPING_INDICATOR_SELECTOR = "#typing-indicator";

function revealMetaChips(container) {
  if (!container || !container.hidden) return;
  const parent = container.closest('.message');
  const start = parent?.offsetHeight;
  container.hidden = false;
  const end = parent?.offsetHeight;
  if (parent && start !== undefined && end !== undefined) {
    parent.style.height = start + 'px';
    parent.offsetHeight; // force reflow
    parent.style.transition = 'height 0.2s ease';
    parent.style.height = end + 'px';
    parent.addEventListener('transitionend', () => {
      parent.style.height = '';
      parent.style.transition = '';
    }, { once: true });
  }
  container.classList.add('chip-enter');
  container.addEventListener('animationend', () => container.classList.remove('chip-enter'), { once: true });
}

export function initChatUI(root = document) {
  const form = root.querySelector("#message-form");
  const textarea = form?.querySelector("textarea");
  const button = form?.querySelector("button");
  const chat = root.querySelector("#chat");
  const errors = document.getElementById("errors");

  if (!form || !textarea || !button || !chat) return;

  // Ensure tag popovers are reinitialized when returning via back navigation
  chat.querySelectorAll('.meta-chips').forEach((chips) => {
    delete chips.dataset.popInit;
  });

  const sessionId = chat.dataset.sessionId;
  const draftKey = `chat-draft-${sessionId}`;
  textarea.value = sessionStorage.getItem(draftKey) || "";

  const handleStopClick = () => {
    console.debug('Stop button clicked');
    const indicator = chat.querySelector(TYPING_INDICATOR_SELECTOR);
    const stopEndpoint = indicator?.dataset.stopUrl;
    const wrap = indicator?.closest('.bot-stream');
    if (wrap) {
      console.debug('Aborting SSE stream');
      wrap.dispatchEvent(new Event('htmx:abort'));
      wrap.removeAttribute('hx-ext');
      wrap.removeAttribute('sse-connect');
      wrap.removeAttribute('sse-close');
      if (indicator) {
        indicator.classList.add('stopped');
        setTimeout(() => indicator.remove(), 1000);
      }
    }
    if (stopEndpoint) {
      console.debug('Sending stop request to', stopEndpoint);
      htmx.ajax('POST', stopEndpoint, { swap: 'none' });
    }
    currentStreamMsgId = null;
    setStreaming(false);
    revealMetaChips(wrap?.querySelector('.meta-chips'));
  };

  const setStreaming = (streaming) => {
    textarea.disabled = streaming;
    if (streaming) {
      console.debug('Entering streaming state for', currentStreamMsgId);
      button.classList.add('stopping');
      button.type = 'button';
      button.disabled = false;
      button.addEventListener('click', handleStopClick, { once: true });
      button.setAttribute('aria-label', 'Stop');
    } else {
      button.classList.remove('stopping');
      button.type = 'submit';
      textarea.disabled = false;
      button.disabled = !textarea.value.trim();
      textarea.focus({ preventScroll: true });
      button.setAttribute('aria-label', 'Send');
    }
  };

  const scrollToBottom = initScrollHandler();
  initStreamHandler(setStreaming, scrollToBottom);

  const findCurrentMsgId = () =>
    chat.querySelector(TYPING_INDICATOR_SELECTOR)?.dataset.msgId || null;

  form.addEventListener("htmx:afterRequest", () => {
    sessionStorage.removeItem(draftKey);
  });

  form.addEventListener("htmx:configRequest", (event) => {
    if (!textarea.value.trim()) {
      event.preventDefault();
      textarea.focus({ preventScroll: true });
    }
  });

  textarea.addEventListener("input", () => {
    sessionStorage.setItem(draftKey, textarea.value);
    if (!currentStreamMsgId) {
      button.disabled = !textarea.value.trim();
    }
  });

  errors?.addEventListener("htmx:afterSwap", () => {
    requestAnimationFrame(() => {
      if (document.querySelector("#errors .error-box")) {
        setStreaming(false);
      }
    });
  });

  chat.addEventListener("htmx:afterSwap", (event) => {
    renderAllMarkdown(chat);
    initTagPopovers(chat);
    if (event.target === chat) {
      currentStreamMsgId = findCurrentMsgId();
      if (currentStreamMsgId) setStreaming(true);
      scrollToBottom(true);
    }
  });

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      m.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) {
          node.classList?.remove("no-anim");
          node.querySelectorAll?.(".no-anim").forEach((el) =>
            el.classList.remove("no-anim")
          );
        }
      });
    }
  });
  observer.observe(chat, { childList: true });

  renderAllMarkdown(chat);
  initTagPopovers(chat);
  currentStreamMsgId = findCurrentMsgId();
  setStreaming(!!currentStreamMsgId);
}

function initScrollHandler(
  containerSelector = "#content-wrapper",
  buttonSelector = "#scroll-bottom"
) {
  const container = document.querySelector(containerSelector);
  const scrollBtn = document.querySelector(buttonSelector);
  const chat = document.querySelector("#chat");
  if (!container || !chat) return () => {};

  const isUserNearBottom = (threshold) => {
    const distanceFromBottom =
      container.scrollHeight - container.clientHeight - container.scrollTop;
    return distanceFromBottom < threshold;
  };

  const toggleScrollBtn = () => {
    if (!scrollBtn) return;
    if (isUserNearBottom(150)) {
      scrollBtn.classList.remove("visible");
    } else {
      scrollBtn.classList.add("visible");
    }
  };

  let autoScrollEnabled = isUserNearBottom();
  let lastScrollTop = container.scrollTop;

  const scrollToBottom = (force = false) => {
    if (force) autoScrollEnabled = true;
    if (autoScrollEnabled || force) {
      container.scrollTo({
        top: container.scrollHeight,
        behavior: "smooth",
      });
    }
    toggleScrollBtn();
  };

  const updateScrollState = (currentTop) => {
    if (currentTop < lastScrollTop - 2) {
      autoScrollEnabled = false;
    } else if (isUserNearBottom(10)) {
      autoScrollEnabled = true;
    }
    lastScrollTop = currentTop;
  };

  container.addEventListener("scroll", () => {
    updateScrollState(container.scrollTop);
    toggleScrollBtn();
  });

  function alignScrollButton() {
    const r = chat.getBoundingClientRect();
    const centerPx = r.left + r.width / 2;
    document.documentElement.style.setProperty('--chat-center', centerPx + 'px');
  }

  // Initial run + keep in sync
  alignScrollButton();
  window.addEventListener('resize', alignScrollButton);
  window.addEventListener('scroll', alignScrollButton, { passive: true });
  new ResizeObserver(alignScrollButton).observe(chat);

  container.addEventListener(
    "wheel",
    (e) => {
      if (e.deltaY < 0) autoScrollEnabled = false;
    },
    { passive: true }
  );

  container.addEventListener(
    "touchmove",
    () => {
      if (container.scrollTop < lastScrollTop) autoScrollEnabled = false;
      lastScrollTop = container.scrollTop;
    },
    { passive: true }
  );

  if (scrollBtn) {
    scrollBtn.addEventListener("click", () => {
      scrollBtn.classList.add("clicked");
      scrollToBottom(true);
      setTimeout(() => scrollBtn.classList.remove("clicked"), 300);
    });
  }
  // Defer initial toggle to ensure layout (and any restored scroll position)
  // are applied before determining visibility. A double rAF gives the
  // browser a chance to paint and then update the button state.
  requestAnimationFrame(() => {
    toggleScrollBtn();
    requestAnimationFrame(toggleScrollBtn);
  });
  return scrollToBottom;
}

function initStreamHandler(setStreaming, scrollToBottom) {
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

      const typing = wrap.querySelector(TYPING_INDICATOR_SELECTOR);
      contentDiv.innerHTML = renderMarkdown(text);
      contentDiv.dataset.rendered = "true";

      if (typing) {
        positionTypingIndicator(contentDiv, typing);
      }
    };

    if (type === "message") {
      scheduleRender(wrap, () => { renderNow(); scrollToBottom(); });
    } else if (type === "error" || type === "done") {
      const rid = sseRenders.get(wrap);
      if (rid) { cancelAnimationFrame(rid); sseRenders.delete(wrap); }
      renderNow();

      const indicator = wrap.querySelector(TYPING_INDICATOR_SELECTOR);
      if (indicator && !indicator.classList.contains('stopped')) {
        indicator.remove();
      }
      wrap.removeAttribute("hx-ext");
      wrap.removeAttribute("sse-connect");
      wrap.removeAttribute("sse-close");
      currentStreamMsgId = null;
      setStreaming(false);
      revealMetaChips(wrap.querySelector('.meta-chips'));
      scrollToBottom();
    }
  };

  document.body.addEventListener("htmx:sseMessage", currentSSEListener);
}
