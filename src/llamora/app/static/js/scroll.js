export function initScrollMemory(wrapperSelector = "#content-wrapper") {
  const wrapperId = wrapperSelector.startsWith("#")
    ? wrapperSelector.slice(1)
    : wrapperSelector;
  const storagePrefix = "scroll-pos";
  const markdownEvent = "markdown:rendered";
  let container = null;
  let scrollListenerAttachedTo = null;
  let markdownListener = null;
  let storageErrorLogged = false;

  const logStorageError = (error) => {
    if (storageErrorLogged) return;
    storageErrorLogged = true;
    if (typeof console !== "undefined" && console.warn) {
      console.warn("Scroll memory storage disabled", error);
    }
  };

  const getStorage = () => {
    try {
      if (typeof window === "undefined") return null;
      return window.sessionStorage ?? null;
    } catch (error) {
      logStorageError(error);
      return null;
    }
  };

  const safeSet = (key, value) => {
    try {
      const storage = getStorage();
      storage?.setItem?.(key, value);
    } catch (error) {
      logStorageError(error);
    }
  };

  const safeGet = (key) => {
    try {
      const storage = getStorage();
      if (!storage?.getItem) return null;
      return storage.getItem(key);
    } catch (error) {
      logStorageError(error);
      return null;
    }
  };

  const safeRemove = (key) => {
    try {
      const storage = getStorage();
      storage?.removeItem?.(key);
    } catch (error) {
      logStorageError(error);
    }
  };

  const getKey = () => {
    const activeDay = document.body?.dataset?.activeDay;
    if (activeDay) {
      return `${storagePrefix}-day-${activeDay}`;
    }
    return `${storagePrefix}-path-${location.pathname}`;
  };

  const onScroll = (event) => {
    const target = event?.target;
    if (!target || typeof target.scrollTop !== "number") {
      return;
    }
    safeSet(getKey(), String(target.scrollTop));
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
    safeSet(getKey(), String(el.scrollTop));
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

      const saved = safeGet(key);
      detachMarkdownListener();
      if (saved !== null) {
        applySavedScroll(saved);
      }
    };

    document.addEventListener(markdownEvent, markdownListener);
  };

  let skipNextRestore = false;

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

    const saved = safeGet(key);
    if (saved !== null) {
      applySavedScroll(saved);
    }
  };

  const maybeRestore = () => {
    if (skipNextRestore) {
      skipNextRestore = false;
      return;
    }
    restore();
  };

  window.addEventListener("app:scroll-target-consumed", (event) => {
    if (event.detail?.target) {
      skipNextRestore = true;
      return;
    }
    maybeRestore();
  });

  restore();

  document.body.addEventListener("htmx:beforeSwap", (evt) => {
    const target = evt.detail?.target || evt.target;
    if (target && target.id === wrapperId) {
      save();
    }
  });

  const resolveWrapperFromNode = (node) => {
    if (!node) return null;

    if (
      typeof DocumentFragment !== "undefined" &&
      node instanceof DocumentFragment
    ) {
      return node.querySelector?.(`#${wrapperId}`) ?? null;
    }

    if (typeof Element !== "undefined" && node instanceof Element) {
      if (node.id === wrapperId) {
        return node;
      }
      return node.querySelector?.(`#${wrapperId}`) ?? null;
    }

    return null;
  };

  const handleLoad = (evt) => {
    const detail = evt.detail ?? {};
    const possibleSources = [detail.item, detail.target, evt.target];

    for (const source of possibleSources) {
      const wrapper = resolveWrapperFromNode(source);
      if (wrapper) {
        maybeRestore();
        return;
      }
    }
  };

  document.body.addEventListener("htmx:load", handleLoad);
  document.body.addEventListener("htmx:historyRestore", handleLoad);
}
