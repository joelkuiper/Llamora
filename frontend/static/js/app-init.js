import { getActiveDay } from "./components/entries-view/active-day-store.js";
import { initRegionFeedback } from "./components/region-feedback.js";
import { initGlobalShortcuts } from "./global-shortcuts.js";
import { ScrollIntent } from "./scroll-intent.js";
import { ScrollManager } from "./scroll-manager.js";
import { registerHydrationOwner } from "./services/hydration-owners.js";
import { handleInvalidationEvent } from "./services/invalidation-bus.js";
import {
  applyRequestTimeHeaders,
  formatTimeElements,
  updateClientToday as syncClientToday,
} from "./services/time.js";
import { getViewState, hydrateViewState } from "./services/view-state.js";
import { createInlineSpinner } from "./ui.js";
import {
  getAlertContainer,
  onAlertDismiss,
  pushAlert,
  registerAlertContainer,
} from "./utils/alert-center.js";
import { runWhenDocumentReady } from "./utils/dom-ready.js";

let headersRegistered = false;
let offlineHandlerRegistered = false;
let scrollManager = null;
let scrollIntent = null;
let resolveAppReady = null;
let entriesLoaderRegistered = false;
let timeFormatterRegistered = false;

export const appReady = new Promise((resolve) => {
  resolveAppReady = resolve;
});

function onRegionSwapped(handler) {
  document.addEventListener("app:region-swapped", (event) => {
    const target = event?.detail?.target;
    handler(target || document, event);
  });
}

function updateClientToday() {
  return syncClientToday();
}

function registerHtmxHeaderHooks(csrfToken) {
  if (headersRegistered) return;
  document.body.addEventListener("htmx:configRequest", (event) => {
    const headers = event.detail?.headers;
    if (!headers) return;

    applyRequestTimeHeaders(headers);

    if (csrfToken) {
      headers["X-CSRFToken"] = csrfToken;
    }

    const activeDate = getActiveDay();
    if (activeDate) {
      headers["X-Active-Day"] = activeDate;
    }

    const viewState = getViewState();
    if (viewState && typeof viewState === "object") {
      headers["X-View-State"] = JSON.stringify(viewState);
    }
  });
  headersRegistered = true;
}

function showServerError() {
  pushAlert({
    id: "network",
    dataset: { alertKind: "network" },
    variant: "danger",
    icon: "⚠️",
    message: "Unable to reach server.",
    autoDismiss: true,
  });
}

function registerOfflineHandler() {
  if (offlineHandlerRegistered) return;
  document.body.addEventListener("htmx:sendError", showServerError);
  offlineHandlerRegistered = true;
}

function registerEntryDeleteAnimationHook() {
  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const detail = event.detail;
    if (!detail?.requestConfig) return;
    const { verb, elt } = detail.requestConfig;
    if (verb !== "delete") return;
    if (!(elt instanceof Element)) return;
    if (!elt.closest(".entry-delete")) return;
    const entry = elt.closest(".entry");
    if (!entry) return;
    entry.classList.remove("motion-animate-entry");
    entry.classList.add("motion-animate-entry-delete");
    entry.dataset.deleteAnimating = "true";
  });

  document.body.addEventListener("htmx:beforeSwap", (event) => {
    const detail = event.detail;
    if (!detail) return;
    const target = detail.target;
    if (!(target instanceof Element)) return;
    const swapStyle = detail.swapStyle || detail.swapSpec?.swapStyle;
    if (swapStyle !== "delete") return;
    if (!target.classList.contains("entry") && !target.dataset.deleteAnimating) return;
    target.classList.add("motion-animate-entry-delete");
    if (typeof detail.swapDelay !== "number" || detail.swapDelay < 180) {
      detail.swapDelay = 180;
    }
    delete target.dataset.deleteAnimating;
  });
}

function initAlertCenter() {
  const container = document.getElementById("errors");
  if (!container) return;
  registerAlertContainer(container);
}

function registerEntriesLoader() {
  if (entriesLoaderRegistered) return;
  let loader = document.getElementById("entries-loading");
  if (!loader) return;

  const getSpinner = (el) => {
    const spinnerEl = el?.querySelector(".entries-loading__spinner");
    return spinnerEl ? createInlineSpinner(spinnerEl) : null;
  };

  let spinner = getSpinner(loader);

  let pending = 0;
  let timerId = null;

  const show = () => {
    if (!loader) return;
    loader.hidden = false;
    loader.dataset.active = "true";
    spinner?.start();
  };

  const hide = () => {
    if (!loader) return;
    loader.hidden = true;
    loader.dataset.active = "false";
    spinner?.stop();
  };

  const clearTimer = () => {
    if (timerId) {
      window.clearTimeout(timerId);
      timerId = null;
    }
  };

  const refreshLoader = () => {
    const next = document.getElementById("entries-loading");
    if (!next || next === loader) {
      return;
    }
    spinner?.stop();
    loader = next;
    spinner = getSpinner(loader);
  };

  const LOADER_TARGET_IDS = new Set(["content-wrapper", "main-content"]);

  const isLoaderTarget = (event) => {
    const target = event?.detail?.target;
    return target instanceof Element && LOADER_TARGET_IDS.has(target.id);
  };

  const start = (event) => {
    if (!isLoaderTarget(event)) {
      return;
    }
    refreshLoader();
    pending += 1;
    if (timerId || loader.dataset.active === "true") {
      return;
    }
    timerId = window.setTimeout(() => {
      timerId = null;
      if (pending > 0) {
        show();
      }
    }, 200);
  };

  const end = (event) => {
    if (!isLoaderTarget(event)) {
      return;
    }
    refreshLoader();
    if (pending > 0) {
      pending -= 1;
    }
    if (pending === 0) {
      clearTimer();
      hide();
    }
  };

  const reset = () => {
    pending = 0;
    clearTimer();
    hide();
  };

  document.body.addEventListener("htmx:beforeRequest", start);
  document.body.addEventListener("htmx:afterRequest", end);
  document.body.addEventListener("htmx:sendError", end);
  document.body.addEventListener("htmx:responseError", end);
  document.body.addEventListener("htmx:afterSwap", (event) => {
    refreshLoader();
    if (isLoaderTarget(event)) {
      pending = 0;
      clearTimer();
      hide();
      return;
    }
    if (pending === 0) {
      hide();
    }
  });

  document.addEventListener("app:rehydrate", () => {
    refreshLoader();
    reset();
  });

  entriesLoaderRegistered = true;
}

function registerTimeFormatter() {
  if (timeFormatterRegistered) return;

  const run = (context) => {
    if (context instanceof DocumentFragment || context instanceof Element) {
      formatTimeElements(context);
      return;
    }
    formatTimeElements(document);
  };

  registerHydrationOwner({
    id: "time-formatter",
    selector: "time.entry-time",
    hydrate: (context) => {
      run(context);
    },
  });

  onRegionSwapped((target) => run(target));

  timeFormatterRegistered = true;
}

function registerViewStateHydration() {
  registerHydrationOwner({
    id: "view-state",
    selector: "#main-content",
    hydrate: (context) => {
      hydrateViewState(context || document);
    },
  });

  onRegionSwapped((target) => hydrateViewState(target));
}

function registerInvalidationBus() {
  document.body.addEventListener("cache:invalidate", handleInvalidationEvent);
}

function ensureScrollManager() {
  if (!scrollManager) {
    scrollManager = new ScrollManager();
    scrollManager.start();
  }
  if (!scrollIntent) {
    scrollIntent = new ScrollIntent(scrollManager);
    scrollIntent.start();
  }
  return scrollManager;
}

export function initGlobalShell() {
  ensureScrollManager();
}

function init() {
  const csrfToken = document.body.dataset.csrfToken || "";
  registerHtmxHeaderHooks(csrfToken);
  registerOfflineHandler();
  registerEntryDeleteAnimationHook();
  registerTimeFormatter();
  registerViewStateHydration();
  registerInvalidationBus();
  initAlertCenter();
  registerEntriesLoader();
  initRegionFeedback();
  initGlobalShell();
  initGlobalShortcuts();

  const resetSharedPopovers = () => {
    const ids = ["tag-popover-global", "tag-detail-popover-global"];
    ids.forEach((id) => {
      const pop = document.getElementById(id);
      if (!pop) return;
      pop.hidden = true;
      pop.removeAttribute("data-floating-ui-placement");
      pop.style.left = "";
      pop.style.top = "";
      pop.style.inset = "";
      pop.style.transform = "";
      pop.style.margin = "";
      pop.classList.remove("htmx-swapping", "htmx-settling", "htmx-request");
      const panel = pop.querySelector(".tp-content");
      panel?.classList.remove("fade-enter", "fade-exit", "pop-enter", "pop-exit");
    });
    document.querySelectorAll(".add-tag-btn.active").forEach((btn) => {
      btn.classList.remove("active");
      btn.setAttribute("aria-expanded", "false");
    });
    document.querySelectorAll("entry-tags.popover-open").forEach((el) => {
      el.classList.remove("popover-open");
    });
  };

  document.addEventListener("app:teardown", resetSharedPopovers);

  updateClientToday();

  const params = new URLSearchParams(window.location.search);
  if (params.get("profile") === "1" && window.htmx) {
    const tab = params.get("profile_tab");
    params.delete("profile");
    params.delete("profile_tab");
    const query = params.toString();
    const nextUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
    window.history.replaceState(history.state, "", nextUrl);
    const url = tab ? `/profile?tab=${encodeURIComponent(tab)}` : "/profile";
    window.htmx.ajax("GET", url, {
      target: "#profile-modal-root",
      swap: "innerHTML",
      source: document.body,
    });
  }

  window.appInit = {
    ...(window.appInit || {}),
    initGlobalShell,
    scroll: ensureScrollManager(),
    updateClientToday,
    alertCenter: {
      getContainer: getAlertContainer,
      onDismiss: onAlertDismiss,
      push: pushAlert,
      register: registerAlertContainer,
    },
  };

  resolveAppReady?.(window.appInit);
}

runWhenDocumentReady(init);
