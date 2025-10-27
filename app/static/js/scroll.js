export function initScrollMemory(wrapperSelector = "#content-wrapper") {
  const wrapperId = wrapperSelector.startsWith("#")
    ? wrapperSelector.slice(1)
    : wrapperSelector;
  const storagePrefix = "scroll-pos";
  const markdownEvent = "markdown:rendered";
  let container = null;
  let scrollListenerAttachedTo = null;
  let markdownListener = null;

  const getKey = () => {
    const chat = document.getElementById("chat");
    if (chat && chat.dataset.date) {
      return `${storagePrefix}-day-${chat.dataset.date}`;
    }
    return `${storagePrefix}-path-${location.pathname}`;
  };

  const onScroll = (event) => {
    const target = event?.target;
    if (!target || typeof target.scrollTop !== "number") {
      return;
    }
    sessionStorage.setItem(getKey(), String(target.scrollTop));
  };

  const ensureContainer = () => {
    const el = document.getElementById(wrapperId);
    if (el === container) {
      return container;
    }

    if (scrollListenerAttachedTo) {
      scrollListenerAttachedTo.removeEventListener("scroll", onScroll);
      scrollListenerAttachedTo = null;
    }

    container = el instanceof HTMLElement ? el : null;

    if (container) {
      container.addEventListener("scroll", onScroll, { passive: true });
      scrollListenerAttachedTo = container;
    }

    return container;
  };

  const save = () => {
    const el = document.getElementById(wrapperId);
    if (!el || typeof el.scrollTop !== "number") return;
    sessionStorage.setItem(getKey(), String(el.scrollTop));
  };

  const detachMarkdownListener = () => {
    if (markdownListener && typeof document !== "undefined") {
      document.removeEventListener(markdownEvent, markdownListener);
    }
    markdownListener = null;
  };

  const needsMarkdownRender = () => {
    if (!container) return false;
    const nodes = container.querySelectorAll?.(".message .markdown-body");
    if (!nodes || nodes.length === 0) return false;

    return Array.from(nodes).some((node) => {
      if (!(node instanceof Element)) return false;
      if (node.dataset.rendered === "true") return false;
      return !node.querySelector?.("#typing-indicator");
    });
  };

  const applySavedScroll = (saved) => {
    const value = Number.parseInt(saved, 10);
    if (!Number.isFinite(value)) return;
    requestAnimationFrame(() => {
      ensureContainer();
      if (!container) return;
      container.scrollTop = value;
    });
  };

  const waitForMarkdown = (key) => {
    if (markdownListener || typeof document === "undefined") return;

    markdownListener = () => {
      ensureContainer();
      if (!container) {
        detachMarkdownListener();
        return;
      }

      if (key !== getKey()) {
        detachMarkdownListener();
        return;
      }

      if (needsMarkdownRender()) {
        return;
      }

      const saved = sessionStorage.getItem(key);
      detachMarkdownListener();
      if (saved !== null) {
        applySavedScroll(saved);
      }
    };

    document.addEventListener(markdownEvent, markdownListener);
  };

  const restore = () => {
    ensureContainer();
    if (!container) return;
    detachMarkdownListener();

    const key = getKey();
    const params = new URLSearchParams(window.location.search);
    const hasTarget =
      params.has("target") ||
      (location.hash && location.hash.startsWith("#msg-"));

    if (hasTarget) {
      return;
    }

    if (needsMarkdownRender()) {
      waitForMarkdown(key);
      return;
    }

    const saved = sessionStorage.getItem(key);
    if (saved !== null) {
      applySavedScroll(saved);
    }
  };

  restore();

  document.body.addEventListener("htmx:beforeSwap", (evt) => {
    const target = evt.detail?.target || evt.target;
    if (target && target.id === wrapperId) {
      save();
    }
  });

  const handleLoad = (evt) => {
    const target = evt.detail?.target || evt.target;
    if (target && target.id === wrapperId) {
      restore();
    }
  };

  document.body.addEventListener("htmx:load", handleLoad);
  document.body.addEventListener("htmx:historyRestore", handleLoad);
}
