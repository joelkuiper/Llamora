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
  },
  "tags-view-list": {
    indicator: "#tags-view-list-loading",
    delayMs: 40,
    spinnerSelector: ".entries-loading__spinner",
    animationClass: "motion-animate-region-enter-soft",
    animationSelector: "[data-tags-view-index]",
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
    });
  }
  return regionState.get(id);
};

const parsePathname = (path) => {
  try {
    return new URL(path, window.location.origin).pathname;
  } catch (_error) {
    return "";
  }
};

const isTagsQuery = (path) => {
  if (!path) return false;
  try {
    const url = new URL(path, window.location.origin);
    if (url.pathname === "/t" || url.pathname.startsWith("/t/")) return true;
    return url.searchParams.get("view") === "tags";
  } catch (_error) {
    return false;
  }
};

const shouldRefreshTagsList = (event) => {
  const path = String(event?.detail?.path || "");
  if (!path) return false;
  if (path.includes("include_list=1")) return true;
  const pathname = parsePathname(path);
  return (pathname === "/t" || pathname.startsWith("/t/")) && isTagsQuery(path);
};

const resolveRegions = (event) => {
  const ids = new Set();
  const target = event?.detail?.target;
  const path = String(event?.detail?.path || "");
  const pathname = parsePathname(path);
  if (target instanceof Element) {
    const id = String(target.id || "").trim();
    if (id && REGION_CONFIG[id]) {
      ids.add(id);
    }
  }

  const source = event?.detail?.requestConfig?.elt;
  if (source instanceof Element && source.closest("#tags-view") && shouldRefreshTagsList(event)) {
    ids.add("tags-view-list");
  }

  if (
    pathname.startsWith("/fragments/tags/") &&
    path.includes("include_list=1") &&
    !ids.has("tags-view-list")
  ) {
    ids.add("tags-view-list");
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
};

const startRegion = (id) => {
  const config = REGION_CONFIG[id];
  if (!config) return;
  const state = getRegionState(id);
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
  const xhr = event?.detail?.xhr;
  if (xhr) {
    requestRegions.set(xhr, regions);
  }
  regions.forEach(startRegion);
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
