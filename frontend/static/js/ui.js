import {
  requestScrollTarget,
  requestScrollTargetConsumed,
} from "./chat/scroll-manager.js";
import {
  animateMotion,
  motionSafeBehavior,
} from "./services/motion.js";

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

const highlightAnimations = new WeakMap();

export function flashHighlight(el) {
  if (!(el instanceof HTMLElement)) return;

  const cancelExisting = highlightAnimations.get(el);
  if (typeof cancelExisting === "function") {
    cancelExisting();
  }

  highlightAnimations.delete(el);

  el.style.backgroundColor = "";
  el.classList.remove("no-anim");
  el.classList.add("highlight");

  const finish = () => {
    el.classList.remove("highlight");
    el.style.backgroundColor = "";
    el.classList.add("no-anim");
    highlightAnimations.delete(el);
  };

  const cancel = animateMotion(el, "motion-animate-highlight", {
    onFinish: finish,
    onCancel: finish,
    reducedMotion: (node, done) => {
      node.style.backgroundColor = "var(--highlight-color)";
      const timeoutId = window.setTimeout(() => {
        node.style.backgroundColor = "";
        done();
      }, 600);
      return () => {
        window.clearTimeout(timeoutId);
        node.style.backgroundColor = "";
      };
    },
  });

  highlightAnimations.set(el, cancel);
}

export function clearScrollTarget(target, options = {}) {
  const { emitEvent = true, historyState = null } = options;
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
    const state = historyState ?? history.state;
    history.replaceState(state, "", finalUrl);
  }

  if (emitEvent) {
    const consumedTarget = target ?? null;
    const meta = { source: "ui" };
    const manager = window.appInit?.scroll ?? null;
    if (manager && typeof manager.notifyTargetConsumed === "function") {
      manager.notifyTargetConsumed(consumedTarget, meta);
    } else {
      requestScrollTargetConsumed(consumedTarget, meta);
    }
  }
}

export function scrollToHighlight(fallbackTarget, options = {}) {
  const {
    targetId = null,
    pushHistory = false,
    scrollOptions = {
      behavior: motionSafeBehavior("smooth"),
      block: "center",
    },
    clearOptions = {},
  } = options;

  const params = new URLSearchParams(window.location.search);
  let target = targetId ?? params.get("target");
  let consumedFallback = false;
  let shouldUpdateHistory = Boolean(targetId);
  const historyState = history.state;

  if (targetId) {
    params.set("target", targetId);
  }

  if (!target && window.location.hash.startsWith("#msg-")) {
    const hashedTarget = window.location.hash.substring(1);
    const hashedElement =
      typeof document?.getElementById === "function"
        ? document.getElementById(hashedTarget)
        : null;

    if (hashedElement) {
      target = hashedTarget;
      params.set("target", target);
      shouldUpdateHistory = true;
    } else if (window.location.hash) {
      const query = params.toString();
      const newUrl = query
        ? `${window.location.pathname}?${query}`
        : window.location.pathname;
      history.replaceState(historyState, "", newUrl);
    }
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

      if (pushHistory && targetId) {
        history.pushState(historyState, "", newUrl);
      } else {
        history.replaceState(historyState, "", newUrl);
      }
    }

    const el = document.getElementById(target);
    if (el) {
      requestScrollTarget(target, scrollOptions, { source: "ui" });
      flashHighlight(el);
      clearScrollTarget(target, { historyState, ...clearOptions });
    }
  }

  if (consumedFallback) {
    const chatView = document.querySelector("chat-view");
    if (chatView?.dataset.scrollTarget === fallbackTarget) {
      delete chatView.dataset.scrollTarget;
    }
  }
}
