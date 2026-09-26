import {
  requestScrollTarget,
  requestScrollTargetConsumed,
  scrollEvents,
} from "./scroll-manager.js";
import { motionSafeBehavior } from "./utils/motion.js";
import { scheduleRafLoop } from "./utils/scheduler.js";
import { animateMotion } from "./utils/transition.js";

export const SPINNER = {
  interval: 80,
  frames: ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"],
};

function spin(el, text = "") {
  let i = 0;
  el.textContent = text ? `${SPINNER.frames[i]} ${text}` : SPINNER.frames[i];
  return setInterval(() => {
    i = (i + 1) % SPINNER.frames.length;
    el.textContent = text ? `${SPINNER.frames[i]} ${text}` : SPINNER.frames[i];
  }, SPINNER.interval);
}

export function createInlineSpinner(element, { text = "" } = {}) {
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
  const id = btn?.dataset.spinnerId;
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

function notifyTargetConsumed(target) {
  const meta = { source: "ui" };
  const manager = window.appInit?.scroll ?? null;
  if (manager && typeof manager.notifyTargetConsumed === "function") {
    manager.notifyTargetConsumed(target, meta);
  } else {
    requestScrollTargetConsumed(target, meta);
  }
}

// Scrolls to and flashes an entry. History is owned by htmx: navigations push
// the canonical URL and pass the target as a one-shot request parameter, so
// this never rewrites the URL.
export function scrollToHighlight(fallbackTarget, options = {}) {
  const {
    targetId = null,
    scrollOptions = {
      behavior: motionSafeBehavior("smooth"),
      block: "center",
    },
    fallbackCleanupDelay = 1500,
    targetPollTimeout = 3000,
  } = options;

  const hashTarget = window.location.hash.startsWith("#entry-")
    ? window.location.hash.substring(1)
    : null;
  const target = targetId || fallbackTarget || hashTarget;
  const consumedFallback = !targetId && Boolean(fallbackTarget);

  let cleanupTimeoutId = null;

  const cleanupFallbackTarget = () => {
    if (!consumedFallback) return;
    if (cleanupTimeoutId !== null) {
      window.clearTimeout(cleanupTimeoutId);
      cleanupTimeoutId = null;
    }

    const entryView = document.querySelector("entry-view");
    if (entryView?.dataset.scrollTarget === fallbackTarget) {
      delete entryView.dataset.scrollTarget;
    }
  };

  const scheduleFallbackCleanup = () => {
    if (!consumedFallback) return;
    if (cleanupTimeoutId !== null) return;

    const delay = Number.isFinite(fallbackCleanupDelay) ? fallbackCleanupDelay : 0;
    if (delay < 0) return;

    cleanupTimeoutId = window.setTimeout(() => cleanupFallbackTarget(), delay);
  };

  if (target) {
    let resolvedHighlight = false;
    let markdownListener = null;
    let htmxListeners = [];
    let poller = null;

    const teardownMarkdownListener = () => {
      if (markdownListener) {
        scrollEvents.removeEventListener("scroll:markdown-complete", markdownListener);
        markdownListener = null;
      }
    };

    const teardownHtmxListeners = () => {
      if (!htmxListeners.length) return;
      for (const { target: evtTarget, type, handler } of htmxListeners) {
        evtTarget?.removeEventListener(type, handler);
      }
      htmxListeners = [];
    };

    const stopPolling = () => {
      if (poller?.active) {
        poller.cancel();
      }
      poller = null;
    };

    const teardownRetries = () => {
      teardownMarkdownListener();
      teardownHtmxListeners();
      stopPolling();
    };

    const highlightTarget = () => {
      const el = document.getElementById(target);
      if (!el) return false;

      resolvedHighlight = true;
      teardownRetries();
      requestScrollTarget(target, scrollOptions, { source: "ui" });
      flashHighlight(el);
      notifyTargetConsumed(target);
      cleanupFallbackTarget();
      return true;
    };

    const attachHtmxListeners = () => {
      const htmxRetryEvents = ["htmx:afterSwap", "htmx:afterSettle"];
      const body = document.body;
      if (!body) return;

      for (const eventName of htmxRetryEvents) {
        const handler = () => {
          if (resolvedHighlight) return;
          highlightTarget();
        };
        body.addEventListener(eventName, handler);
        htmxListeners.push({ target: body, type: eventName, handler });
      }
    };

    const pollForTarget = () => {
      const timeout =
        Number.isFinite(targetPollTimeout) && targetPollTimeout > 0 ? targetPollTimeout : 0;
      if (timeout <= 0) return;

      poller = scheduleRafLoop({
        timeoutMs: timeout,
        callback: ({ timedOut }) => {
          if (resolvedHighlight) {
            return false;
          }
          if (timedOut) {
            teardownRetries();
            return false;
          }
          return !highlightTarget();
        },
      });
    };

    if (!highlightTarget()) {
      markdownListener = () => {
        if (resolvedHighlight) return;
        highlightTarget();
        teardownMarkdownListener();
      };
      scrollEvents.addEventListener("scroll:markdown-complete", markdownListener);
      attachHtmxListeners();
      pollForTarget();
      scheduleFallbackCleanup();
    }
  }
}
