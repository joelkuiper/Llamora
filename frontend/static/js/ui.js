import { scrollEvents } from "./chat/scroll-manager.js";
import { motionSafeBehavior, prefersReducedMotion } from "./utils/motion.js";

export const SPINNER = {
  interval: 80,
  frames: ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"],
};

function spin(el, text = "") {
  let i = 0;
  el.textContent = text
    ? `${SPINNER.frames[i]} ${text}`
    : SPINNER.frames[i];
  return setInterval(() => {
    i = (i + 1) % SPINNER.frames.length;
    el.textContent = text
      ? `${SPINNER.frames[i]} ${text}`
      : SPINNER.frames[i];
  }, SPINNER.interval);
}

export function createInlineSpinner(
  element,
  { text = "" } = {}
) {
  let spinnerEl = element || null;
  let intervalId = null;

  const start = () => {
    if (!spinnerEl || intervalId !== null) return;
    spinnerEl.classList.add("htmx-request");
    intervalId = spin(spinnerEl, text);
  };

  const stop = () => {
    if (intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }
    if (spinnerEl) {
      spinnerEl.classList.remove("htmx-request");
      spinnerEl.textContent = "";
    }
  };

  const setElement = (nextEl) => {
    if (nextEl === spinnerEl) return;
    stop();
    spinnerEl = nextEl || null;
  };

  return {
    start,
    stop,
    setElement,
  };
}

export function startButtonSpinner(btn, loadingText = "Loading") {
  if (!btn || btn.dataset.spinning === "1") return;
  const originalText = btn.textContent;
  btn.dataset.spinning = "1";
  btn.dataset.originalText = originalText;
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.textContent = "";
  const spinnerEl = document.createElement("span");
  spinnerEl.className = "spinner";
  spinnerEl.setAttribute("aria-hidden", "true");
  btn.appendChild(spinnerEl);
  btn.append(" ", loadingText);
  const id = spin(spinnerEl);
  btn.dataset.spinnerId = String(id);
  return id;
}

export function stopButtonSpinner(btn) {
  const id = btn && btn.dataset.spinnerId;
  if (id) clearInterval(Number(id));
  if (btn && "originalText" in btn.dataset) {
    btn.textContent = btn.dataset.originalText;
    btn.removeAttribute("data-original-text");
  }
  if (btn) {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.removeAttribute("data-spinner-id");
    btn.removeAttribute("data-spinning");
  }
}

export function flashHighlight(el) {
  if (!(el instanceof HTMLElement)) return;
  const existing = el.dataset.flashTimerId;
  if (existing) {
    window.clearTimeout(Number(existing));
    delete el.dataset.flashTimerId;
  }
  el.classList.remove("no-anim");
  el.classList.add("highlight");

  if (prefersReducedMotion()) {
    el.style.backgroundColor = "var(--highlight-color)";
    const timeoutId = window.setTimeout(() => {
      el.classList.remove("highlight");
      el.style.backgroundColor = "";
      el.classList.add("no-anim");
      delete el.dataset.flashTimerId;
    }, 600);
    el.dataset.flashTimerId = String(timeoutId);
    return;
  }

  el.style.animation = "flash 1s ease-in-out";
  el.addEventListener(
    "animationend",
    () => {
      el.classList.remove("highlight");
      el.style.animation = "";
      el.classList.add("no-anim");
      delete el.dataset.flashTimerId;
    },
    { once: true }
  );
}

export function clearScrollTarget(target, options = {}) {
  const { emitEvent = true } = options;
  const params = new URLSearchParams(window.location.search);
  const hadTargetParam = params.has("target");
  if (hadTargetParam) {
    params.delete("target");
  }

  const highlightHash = target ? `#${target}` : "";
  const shouldClearHash =
    Boolean(highlightHash) && window.location.hash === highlightHash;

  if (hadTargetParam || shouldClearHash) {
    const query = params.toString();
    const baseUrl = query
      ? `${window.location.pathname}?${query}`
      : window.location.pathname;
    const finalUrl = shouldClearHash ? baseUrl : `${baseUrl}${window.location.hash}`;
    history.replaceState(null, "", finalUrl);
  }

  if (emitEvent) {
    const detail = { target: target ?? null };
    const manager = window.appInit?.scroll ?? null;
    if (manager && typeof manager.notifyTargetConsumed === "function") {
      manager.notifyTargetConsumed(detail.target);
    } else {
      scrollEvents.dispatchEvent(
        new CustomEvent("scroll:target-consumed", { detail })
      );
    }
  }
}

export function scrollToHighlight(fallbackTarget) {
  const params = new URLSearchParams(window.location.search);
  let target = params.get("target");
  let consumedFallback = false;
  let shouldUpdateHistory = false;

  if (!target && window.location.hash.startsWith("#msg-")) {
    target = window.location.hash.substring(1);
    params.set("target", target);
    shouldUpdateHistory = true;
  }

  if (!target && fallbackTarget) {
    target = fallbackTarget;
    params.set("target", target);
    shouldUpdateHistory = true;
    consumedFallback = true;
  }

  if (target) {
    if (shouldUpdateHistory || window.location.hash) {
      const query = params.toString();
      const newUrl = query
        ? `${window.location.pathname}?${query}`
        : window.location.pathname;
      history.replaceState(null, "", newUrl);
    }
    const el = document.getElementById(target);
    if (el) {
      scrollEvents.dispatchEvent(
        new CustomEvent("scroll:target", {
          detail: {
            id: target,
            options: {
              behavior: motionSafeBehavior("smooth"),
              block: "center",
            },
          },
        })
      );
      flashHighlight(el);
      clearScrollTarget(target);
    }
  }

  if (consumedFallback) {
    const chatView = document.querySelector("chat-view");
    if (chatView?.dataset.scrollTarget === fallbackTarget) {
      delete chatView.dataset.scrollTarget;
    }
  }
}
