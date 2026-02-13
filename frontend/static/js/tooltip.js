import { autoUpdate, computePosition, flip, offset, shift } from "./vendor/setup-globals.js";

const SHOW_DELAY_MS = 320;

let tooltipEl = null;
let innerEl = null;
let activeTrigger = null;
let pendingTrigger = null;
let delayTimer = null;
let cleanupAutoUpdate = null;
let initialized = false;

function ensureElement() {
  if (tooltipEl?.isConnected) return;
  tooltipEl = document.createElement("div");
  tooltipEl.className = "tooltip";
  tooltipEl.innerHTML = '<div class="tooltip-inner"></div>';
  innerEl = tooltipEl.querySelector(".tooltip-inner");
  tooltipEl.hidden = true;
  document.body.appendChild(tooltipEl);
}

function findTrigger(node) {
  if (!(node instanceof Element)) return null;
  return node.closest("[data-tooltip-title]");
}

function isSuppressed(trigger) {
  if (!trigger) return true;
  if (trigger.dataset.tooltipDisabled === "true") return true;
  if (trigger.classList.contains("active")) return true;
  if (trigger.getAttribute("aria-expanded") === "true") return true;
  if (trigger.matches(":disabled") || trigger.getAttribute("aria-disabled") === "true") return true;
  return false;
}

function getTrigger(node) {
  const trigger = findTrigger(node);
  return trigger && !isSuppressed(trigger) ? trigger : null;
}

function parseFloat_(value, fallback) {
  const n = Number.parseFloat(value);
  return Number.isFinite(n) ? n : fallback;
}

function updatePosition(trigger) {
  const placement = trigger.dataset.tooltipPlacement || "bottom";
  const ox = parseFloat_(trigger.dataset.tooltipOffsetX, 0);
  const oy = parseFloat_(trigger.dataset.tooltipOffsetY, 8);

  computePosition(trigger, tooltipEl, {
    placement,
    strategy: "fixed",
    middleware: [offset({ mainAxis: oy, crossAxis: ox }), flip(), shift({ padding: 8 })],
  }).then(({ x, y }) => {
    if (activeTrigger !== trigger) return;
    tooltipEl.style.left = `${x}px`;
    tooltipEl.style.top = `${y}px`;
  });
}

function dismiss() {
  if (delayTimer !== null) {
    clearTimeout(delayTimer);
    delayTimer = null;
  }
  pendingTrigger = null;

  if (cleanupAutoUpdate) {
    cleanupAutoUpdate();
    cleanupAutoUpdate = null;
  }

  if (tooltipEl) {
    tooltipEl.classList.remove("visible");
    tooltipEl.hidden = true;
  }

  activeTrigger = null;
}

function show(trigger) {
  if (activeTrigger === trigger) return;
  if (!trigger?.isConnected || isSuppressed(trigger)) return;

  const title = trigger.dataset.tooltipTitle?.trim();
  if (!title) return;

  dismiss();
  ensureElement();

  innerEl.textContent = title;
  tooltipEl.style.position = "fixed";
  tooltipEl.hidden = false;
  activeTrigger = trigger;

  updatePosition(trigger);
  cleanupAutoUpdate = autoUpdate(trigger, tooltipEl, () => updatePosition(trigger));

  requestAnimationFrame(() => {
    if (activeTrigger === trigger) {
      tooltipEl.classList.add("visible");
    }
  });
}

function scheduleShow(trigger) {
  if (!trigger || activeTrigger === trigger) return;

  // When switching between triggers, show immediately (no delay).
  if (activeTrigger) {
    show(trigger);
    return;
  }

  // If already pending for this trigger, let the timer run.
  if (pendingTrigger === trigger) return;

  // Clear any previous pending.
  if (delayTimer !== null) {
    clearTimeout(delayTimer);
    delayTimer = null;
  }
  pendingTrigger = trigger;

  delayTimer = setTimeout(() => {
    delayTimer = null;
    if (pendingTrigger !== trigger) return;
    pendingTrigger = null;
    if (!trigger.isConnected || !trigger.matches(":hover")) return;
    show(trigger);
  }, SHOW_DELAY_MS);
}

export function initTooltips() {
  if (initialized) return;
  initialized = true;

  document.addEventListener(
    "pointerover",
    (e) => {
      const trigger = getTrigger(e.target);
      if (!trigger) return;
      // Ignore moves within the same trigger.
      if (e.relatedTarget instanceof Node && trigger.contains(e.relatedTarget)) return;
      scheduleShow(trigger);
    },
    true,
  );

  document.addEventListener(
    "pointerout",
    (e) => {
      const trigger = findTrigger(e.target);
      if (!trigger) return;
      // Ignore moves within the same trigger.
      if (e.relatedTarget instanceof Node && trigger.contains(e.relatedTarget)) return;

      // If moving to another trigger, let pointerover handle it.
      // Only dismiss if we're leaving our active/pending trigger.
      if (pendingTrigger === trigger) {
        clearTimeout(delayTimer);
        delayTimer = null;
        pendingTrigger = null;
      }
      if (activeTrigger === trigger) {
        dismiss();
      }
    },
    true,
  );

  document.addEventListener("focusin", (e) => {
    const trigger = getTrigger(e.target);
    if (!trigger) return;
    try {
      if (!trigger.matches(":focus-visible")) return;
    } catch (_) {
      /* proceed */
    }
    show(trigger);
  });

  document.addEventListener("focusout", (e) => {
    const trigger = findTrigger(e.target);
    if (!trigger || activeTrigger !== trigger) return;
    if (e.relatedTarget instanceof Node && trigger.contains(e.relatedTarget)) return;
    dismiss();
  });

  document.addEventListener("pointerdown", dismiss, true);
  document.addEventListener("click", dismiss, true);
  document.addEventListener("scroll", dismiss, true);
  window.addEventListener("resize", dismiss, { passive: true });
  window.addEventListener("blur", dismiss, { passive: true });
  window.addEventListener("popstate", dismiss);

  for (const evt of [
    "htmx:beforeRequest",
    "htmx:beforeSwap",
    "htmx:afterSwap",
    "htmx:afterSettle",
    "app:rehydrate",
  ]) {
    document.addEventListener(evt, dismiss);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) dismiss();
  });
}

initTooltips();
