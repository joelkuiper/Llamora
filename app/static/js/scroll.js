export function initScrollMemory(wrapperSelector = "#content-wrapper") {
  const wrapperId = wrapperSelector.startsWith("#") ? wrapperSelector.slice(1) : wrapperSelector;
  let container = document.getElementById(wrapperId);
  const storagePrefix = "scroll-pos";

  const getKey = () => {
    const chat = document.getElementById("chat");
    if (chat && chat.dataset.date) {
      return `${storagePrefix}-day-${chat.dataset.date}`;
    }
    return `${storagePrefix}-path-${location.pathname}`;
  };

  const save = () => {
    if (!container) return;
    sessionStorage.setItem(getKey(), String(container.scrollTop));
  };

  const restore = () => {
    container = document.getElementById(wrapperId);
    if (!container) return;
    const key = getKey();
    const params = new URLSearchParams(window.location.search);
    const hasTarget = params.has("target") || (location.hash && location.hash.startsWith("#msg-"));
    if (!hasTarget) {
      const saved = sessionStorage.getItem(key);
      if (saved !== null) {
        requestAnimationFrame(() => {
          container.scrollTop = parseInt(saved, 10);
        });
      }
    }
    container.addEventListener("scroll", () => {
      sessionStorage.setItem(getKey(), String(container.scrollTop));
    }, { passive: true });
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
