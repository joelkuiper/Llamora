import { createTooltipPopover } from "./utils/tooltip-popover.js";

const TOOLTIP_DELAY_MS = 320;
const TOOLTIP_EXIT_MS = 180;

let tooltipEl;
let innerEl;
let tooltipPopover;
let activeTrigger;
let pendingTrigger;
let showTimer;
let hideTimer;
let pendingHidePopover;
let initialized = false;
let showToken = 0;

function parseOffset(value, fallback) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function ensureTooltipElement() {
  if (tooltipEl?.isConnected) return;

  tooltipEl = document.createElement("div");
  tooltipEl.className = "tooltip";
  tooltipEl.innerHTML = '<div class="tooltip-inner"></div>';
  innerEl = tooltipEl.querySelector(".tooltip-inner");
  tooltipEl.hidden = true;
  document.body.appendChild(tooltipEl);
}

function findTooltipTrigger(node) {
  if (!(node instanceof Element)) return null;
  return node.closest("[data-tooltip-title]");
}

function shouldSuppressTooltip(trigger) {
  if (!trigger) return true;
  if (trigger.dataset.tooltipDisabled === "true") return true;
  if (trigger.classList.contains("active")) return true;
  if (trigger.getAttribute("aria-expanded") === "true") return true;
  if (trigger.matches(":disabled") || trigger.getAttribute("aria-disabled") === "true") return true;
  return false;
}

function getTooltipTrigger(node) {
  const trigger = findTooltipTrigger(node);
  if (!trigger || shouldSuppressTooltip(trigger)) return null;
  return trigger;
}

function isFocusVisible(trigger) {
  if (!trigger || !(trigger instanceof Element)) return false;
  try {
    return trigger.matches(":focus-visible");
  } catch (_error) {
    return true;
  }
}

function clearShowTimer() {
  if (!showTimer) return;
  clearTimeout(showTimer);
  showTimer = null;
}

function clearHideTimer({ cleanup = false } = {}) {
  if (hideTimer) {
    clearTimeout(hideTimer);
    hideTimer = null;
  }
  if (cleanup && pendingHidePopover) {
    void pendingHidePopover.cleanup();
    pendingHidePopover = null;
  }
}

function clearPendingTrigger() {
  pendingTrigger = null;
  showToken += 1;
  clearShowTimer();
}

function cleanupPopover() {
  const popover = tooltipPopover;
  tooltipPopover = null;
  activeTrigger = null;
  if (!popover) return;
  void popover.cleanup();
}

function hideTooltip() {
  clearPendingTrigger();
  clearHideTimer({ cleanup: true });

  const popover = tooltipPopover;
  tooltipPopover = null;
  activeTrigger = null;
  pendingHidePopover = popover || null;

  if (!tooltipEl) {
    clearHideTimer({ cleanup: true });
    return;
  }

  tooltipEl.classList.remove("visible");
  if (!pendingHidePopover) {
    tooltipEl.hidden = true;
    return;
  }

  hideTimer = window.setTimeout(() => {
    hideTimer = null;
    if (pendingTrigger || activeTrigger || tooltipPopover) return;
    tooltipEl.hidden = true;
    const currentPopover = pendingHidePopover;
    pendingHidePopover = null;
    if (currentPopover) {
      void currentPopover.cleanup();
    }
  }, TOOLTIP_EXIT_MS);
}

function showTooltip(trigger) {
  if (!trigger?.isConnected || shouldSuppressTooltip(trigger)) return;

  const title = trigger.dataset.tooltipTitle?.trim();
  if (!title) return;

  clearPendingTrigger();
  clearHideTimer({ cleanup: true });
  if (activeTrigger === trigger && tooltipPopover) return;

  cleanupPopover();
  ensureTooltipElement();

  innerEl.textContent = title;
  tooltipEl.hidden = false;

  const placement = trigger.dataset.tooltipPlacement || "bottom";
  const offsetX = parseOffset(trigger.dataset.tooltipOffsetX, 0);
  const offsetY = parseOffset(trigger.dataset.tooltipOffsetY, 8);

  tooltipPopover = createTooltipPopover(trigger, tooltipEl, {
    placement,
    offset: [offsetX, offsetY],
    onShow: () => {
      tooltipEl.classList.add("visible");
    },
    onHide: () => {
      tooltipEl.classList.remove("visible");
    },
    onHidden: () => {
      tooltipEl.hidden = true;
    },
  });
  tooltipPopover.controller.show();
  activeTrigger = trigger;
}

function scheduleTooltip(trigger) {
  if (!trigger || activeTrigger === trigger) return;

  clearPendingTrigger();
  pendingTrigger = trigger;
  showToken += 1;
  const token = showToken;
  showTimer = window.setTimeout(() => {
    showTimer = null;
    if (token !== showToken) return;
    if (pendingTrigger !== trigger) return;
    if (!trigger.isConnected || !trigger.matches(":hover")) return;
    showTooltip(trigger);
  }, TOOLTIP_DELAY_MS);
}

export function initTooltips() {
  if (initialized) return;
  initialized = true;

  document.addEventListener(
    "pointerover",
    (event) => {
      const trigger = getTooltipTrigger(event.target);
      if (!trigger) return;
      const related = event.relatedTarget;
      if (related instanceof Node && trigger.contains(related)) return;
      scheduleTooltip(trigger);
    },
    true,
  );

  document.addEventListener(
    "pointerout",
    (event) => {
      const trigger = findTooltipTrigger(event.target);
      if (!trigger) return;
      const related = event.relatedTarget;
      if (related instanceof Node && trigger.contains(related)) return;
      if (pendingTrigger === trigger) {
        clearPendingTrigger();
      }
      if (activeTrigger === trigger) {
        hideTooltip();
      }
    },
    true,
  );

  document.addEventListener("focusin", (event) => {
    const trigger = getTooltipTrigger(event.target);
    if (!trigger) return;
    if (!isFocusVisible(trigger)) return;
    showTooltip(trigger);
  });

  document.addEventListener("focusout", (event) => {
    const trigger = findTooltipTrigger(event.target);
    if (!trigger || activeTrigger !== trigger) return;
    const related = event.relatedTarget;
    if (related instanceof Node && trigger.contains(related)) return;
    hideTooltip();
  });

  document.addEventListener("pointerdown", hideTooltip, true);
  document.addEventListener("click", hideTooltip, true);
  document.addEventListener("scroll", hideTooltip, true);
  window.addEventListener("resize", hideTooltip, { passive: true });
  window.addEventListener("blur", hideTooltip, { passive: true });
  window.addEventListener("popstate", hideTooltip);

  const htmxHideEvents = [
    "htmx:beforeRequest",
    "htmx:beforeSwap",
    "htmx:afterSwap",
    "htmx:afterSettle",
    "app:rehydrate",
  ];
  htmxHideEvents.forEach((eventName) => {
    document.addEventListener(eventName, hideTooltip);
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      hideTooltip();
    }
  });
}

initTooltips();
