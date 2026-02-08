import { createTooltipPopover } from "./utils/tooltip-popover.js";

let tooltipEl;
let innerEl;
let tooltipPopover;
let currentTarget;
let lastRect;
let initialized = false;

function parseOffset(value, fallback) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function ensureEl() {
  if (tooltipEl?.isConnected) return;
  tooltipEl = document.createElement("div");
  tooltipEl.className = "tooltip";
  tooltipEl.innerHTML = '<div class="tooltip-inner"></div>';
  innerEl = tooltipEl.querySelector(".tooltip-inner");
  document.body.appendChild(tooltipEl);
  tooltipEl.hidden = true;
}

function show(el) {
  ensureEl();
  hide();
  innerEl.textContent = el.dataset.tooltipTitle || "";
  Object.assign(tooltipEl.style, {
    top: "",
    left: "",
    position: "",
  });
  const placement = el.dataset.tooltipPlacement || "bottom";
  const offsetX = parseOffset(el.dataset.tooltipOffsetX, 0);
  const offsetY = parseOffset(el.dataset.tooltipOffsetY, 8);

  tooltipPopover = createTooltipPopover(el, tooltipEl, {
    placement,
    offset: [offsetX, offsetY],
    onShow: () => {
      tooltipEl.classList.add("visible");
    },
    onHide: () => {
      tooltipEl.classList.remove("visible");
      const rect = tooltipEl.getBoundingClientRect();
      lastRect = { top: rect.top, left: rect.left };
    },
    onHidden: () => {
      tooltipEl.hidden = true;
      if (lastRect) {
        Object.assign(tooltipEl.style, {
          top: `${lastRect.top}px`,
          left: `${lastRect.left}px`,
          position: "fixed",
        });
        lastRect = null;
      }
    },
  });
  tooltipPopover.controller.show();
  currentTarget = el;
}

function hide() {
  if (!tooltipPopover) {
    if (tooltipEl) {
      tooltipEl.classList.remove("visible");
      tooltipEl.hidden = true;
    }
    currentTarget = null;
    lastRect = null;
    return Promise.resolve();
  }
  const currentPopover = tooltipPopover;
  tooltipPopover = null;
  currentTarget = null;
  return currentPopover.cleanup();
}

export function initTooltips() {
  if (initialized) return;
  initialized = true;

  const getTrigger = (el) => {
    const trigger = el.closest("[data-tooltip-title]");
    return trigger && !trigger.classList.contains("active") ? trigger : null;
  };

  document.addEventListener("mouseover", (e) => {
    const trigger = getTrigger(e.target);
    if (!trigger || trigger === currentTarget) return;
    show(trigger);
  });

  document.addEventListener("mouseout", (e) => {
    if (!currentTarget) return;
    if (e.relatedTarget && currentTarget.contains(e.relatedTarget)) return;
    if (getTrigger(e.target) !== currentTarget) return;
    hide();
  });

  document.addEventListener("focusin", (e) => {
    const trigger = getTrigger(e.target);
    if (trigger) show(trigger);
  });

  document.addEventListener("focusout", (e) => {
    if (currentTarget && getTrigger(e.target) === currentTarget) hide();
  });

  document.addEventListener("click", hide);
  document.addEventListener("htmx:beforeSwap", hide);
  document.addEventListener("htmx:afterSwap", hide);
}

initTooltips();
