import { createInlineSpinner } from "../ui.js";
import { animateMotion } from "../utils/transition.js";

const BOOT_KEY = "__llamoraRegionFeedbackBooted";

const REGION_CONFIG = {
  "content-wrapper": {
    indicator: "#entries-loading",
    delayMs: 180,
    spinnerSelector: ".entries-loading__spinner",
    animationClass: "motion-animate-region-enter",
  },
  "main-content": {
    indicator: "#entries-loading",
    delayMs: 180,
    spinnerSelector: ".entries-loading__spinner",
    animationClass: "motion-animate-region-enter",
  },
  "tags-view-detail": {
    indicator: "#tags-view-detail-loading",
    delayMs: 80,
    spinnerSelector: ".entries-loading__spinner",
    animationClass: "motion-animate-region-enter",
    animationSelector: ".tags-view__detail-inner",
    // No anchorSelector: the loader is position:absolute inside a stable
    // .tags-view__detail-area wrapper, so CSS handles geometry — no JS needed.
  },
};

const requestRegions = new WeakMap();
const regionState = new Map();

const getRegionState = (id) => {
  if (!regionState.has(id)) {
    regionState.set(id, {
      pending: 0,
      timerId: null,
      indicatorEl: null,
      spinner: null,
      anchorRect: null,
    });
  }
  return regionState.get(id);
};

const resolveRegions = (event) => {
  const ids = new Set();
  const target = event?.detail?.target;
  if (target instanceof Element) {
    const id = String(target.id || "").trim();
    if (id && REGION_CONFIG[id]) {
      ids.add(id);
    }
  }

  return ids;
};

const ensureIndicator = (id) => {
  const config = REGION_CONFIG[id];
  if (!config) return null;
  const state = getRegionState(id);
  const next = document.querySelector(config.indicator);
  if (!(next instanceof HTMLElement)) {
    state.indicatorEl = null;
    state.spinner?.setElement?.(null);
    return null;
  }
  if (state.indicatorEl !== next) {
    state.indicatorEl = next;
    const spinnerEl = next.querySelector(config.spinnerSelector || "");
    if (!state.spinner) {
      state.spinner = createInlineSpinner(spinnerEl);
    } else {
      state.spinner.setElement(spinnerEl);
    }
  }
  return state.indicatorEl;
};

const showIndicator = (id) => {
  const state = getRegionState(id);
  const indicator = ensureIndicator(id);
  if (!(indicator instanceof HTMLElement)) return;
  applyAnchoredFrame(id);
  indicator.hidden = false;
  indicator.dataset.active = "true";
  state.spinner?.start?.();
};

const hideIndicator = (id) => {
  const state = getRegionState(id);
  const indicator = ensureIndicator(id);
  if (!(indicator instanceof HTMLElement)) return;
  indicator.hidden = true;
  indicator.dataset.active = "false";
  state.spinner?.stop?.();
  clearAnchoredFrame(id);
  state.anchorRect = null;
};

const toRectSnapshot = (rect) => {
  if (!rect) return null;
  const width = Math.round(rect.width || 0);
  const height = Math.round(rect.height || 0);
  if (width < 1 || height < 1) return null;
  return {
    left: Math.round(rect.left || 0),
    top: Math.round(rect.top || 0),
    width,
    height,
  };
};

const readAnchorRect = (id, fallbackTarget = null) => {
  const config = REGION_CONFIG[id];
  const state = getRegionState(id);
  const anchorSelector = String(config?.anchorSelector || "").trim();
  if (!anchorSelector) return null;
  const anchor = document.querySelector(anchorSelector);
  if (anchor instanceof HTMLElement) {
    const snapshot = toRectSnapshot(anchor.getBoundingClientRect());
    if (snapshot) {
      state.anchorRect = snapshot;
      return snapshot;
    }
  }
  if (fallbackTarget instanceof HTMLElement) {
    const snapshot = toRectSnapshot(fallbackTarget.getBoundingClientRect());
    if (snapshot) {
      state.anchorRect = snapshot;
      return snapshot;
    }
  }
  return state.anchorRect;
};

const applyAnchoredFrame = (id, fallbackTarget = null) => {
  const config = REGION_CONFIG[id];
  const anchorSelector = String(config?.anchorSelector || "").trim();
  if (!anchorSelector) return;
  const indicator = ensureIndicator(id);
  if (!(indicator instanceof HTMLElement)) return;
  const rect = readAnchorRect(id, fallbackTarget);
  if (!rect) return;
  indicator.style.left = `${rect.left}px`;
  indicator.style.top = `${rect.top}px`;
  indicator.style.width = `${rect.width}px`;
  indicator.style.height = `${rect.height}px`;
};

const clearAnchoredFrame = (id) => {
  const config = REGION_CONFIG[id];
  if (!config?.anchorSelector) return;
  const indicator = ensureIndicator(id);
  if (!(indicator instanceof HTMLElement)) return;
  indicator.style.removeProperty("left");
  indicator.style.removeProperty("top");
  indicator.style.removeProperty("width");
  indicator.style.removeProperty("height");
};

const startRegion = (id, target = null) => {
  const config = REGION_CONFIG[id];
  if (!config) return;
  const state = getRegionState(id);
  applyAnchoredFrame(id, target);
  state.pending += 1;
  if (state.pending > 1) return;
  if (state.timerId) {
    window.clearTimeout(state.timerId);
  }
  state.timerId = window.setTimeout(() => {
    state.timerId = null;
    if (state.pending > 0) {
      showIndicator(id);
    }
  }, config.delayMs ?? 0);
};

const endRegion = (id) => {
  const config = REGION_CONFIG[id];
  if (!config) return;
  const state = getRegionState(id);
  if (state.pending > 0) {
    state.pending -= 1;
  }
  if (state.pending > 0) return;
  if (state.timerId) {
    window.clearTimeout(state.timerId);
    state.timerId = null;
  }
  hideIndicator(id);
};

const resetRegions = () => {
  for (const id of Object.keys(REGION_CONFIG)) {
    const state = getRegionState(id);
    state.pending = 0;
    if (state.timerId) {
      window.clearTimeout(state.timerId);
      state.timerId = null;
    }
    hideIndicator(id);
  }
};

const refreshAnchoredRegions = () => {
  for (const id of Object.keys(REGION_CONFIG)) {
    const config = REGION_CONFIG[id];
    if (!config?.anchorSelector) continue;
    const state = getRegionState(id);
    if (state.pending <= 0) continue;
    applyAnchoredFrame(id);
  }
};

let refreshRaf = 0;
const scheduleAnchoredRefresh = () => {
  if (refreshRaf) return;
  refreshRaf = window.requestAnimationFrame(() => {
    refreshRaf = 0;
    refreshAnchoredRegions();
  });
};

const animateRegionSwap = (event) => {
  const target = event?.detail?.target;
  if (!(target instanceof HTMLElement)) return;
  const config = REGION_CONFIG[target.id];
  if (!config?.animationClass) return;
  const node = config.animationSelector ? target.querySelector(config.animationSelector) : target;
  if (!(node instanceof HTMLElement)) return;
  animateMotion(node, config.animationClass);
};

const trackRequestStart = (event) => {
  const regions = resolveRegions(event);
  if (!regions.size) return;
  const target = event?.detail?.target;
  const xhr = event?.detail?.xhr;
  if (xhr) {
    requestRegions.set(xhr, regions);
  }
  regions.forEach((id) => {
    startRegion(id, target);
  });
};

const trackRequestEnd = (event) => {
  const xhr = event?.detail?.xhr;
  const regions = xhr ? requestRegions.get(xhr) : null;
  const resolved = regions?.size ? regions : resolveRegions(event);
  if (!resolved.size) return;
  resolved.forEach(endRegion);
  if (xhr) {
    requestRegions.delete(xhr);
  }
};

export function initRegionFeedback() {
  if (globalThis[BOOT_KEY]) return;
  globalThis[BOOT_KEY] = true;

  document.body.addEventListener("htmx:beforeRequest", trackRequestStart);
  document.body.addEventListener("htmx:afterRequest", trackRequestEnd);
  document.body.addEventListener("htmx:sendError", trackRequestEnd);
  document.body.addEventListener("htmx:responseError", trackRequestEnd);
  document.addEventListener("app:region-swapped", animateRegionSwap);

  document.addEventListener("app:rehydrate", resetRegions);
  document.addEventListener("app:teardown", resetRegions);
}
