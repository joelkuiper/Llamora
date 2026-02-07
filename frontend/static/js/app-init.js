import { ScrollManager } from "./entries/scroll-manager.js";
import { ScrollIntent } from "./entries/scroll-intent.js";
import { initGlobalShortcuts } from "./global-shortcuts.js";
import { getActiveDay } from "./entries/active-day-store.js";
import { createInlineSpinner } from "./ui.js";
import {
  getAlertContainer,
  onAlertDismiss,
  pushAlert,
  registerAlertContainer,
} from "./utils/alert-center.js";
import { runWhenDocumentReady } from "./utils/dom-ready.js";
import {
  applyRequestTimeHeaders,
  updateClientToday as syncClientToday,
} from "./services/time.js";

let headersRegistered = false;
let offlineHandlerRegistered = false;
let scrollManager = null;
let scrollIntent = null;
let resolveAppReady = null;
let entriesLoaderRegistered = false;

export const appReady = new Promise((resolve) => {
  resolveAppReady = resolve;
});

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

function initAlertCenter() {
  const container = document.getElementById("errors");
  if (!container) return;
  registerAlertContainer(container);
}

function registerEntriesLoader() {
  if (entriesLoaderRegistered) return;
  const loader = document.getElementById("entries-loading");
  if (!loader) return;

  const spinnerEl = loader.querySelector(".entries-loading__spinner");
  const spinner = spinnerEl ? createInlineSpinner(spinnerEl) : null;

  let pending = 0;
  let timerId = null;

  const show = () => {
    loader.hidden = false;
    loader.dataset.active = "true";
    spinner?.start();
  };

  const hide = () => {
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

  const start = (event) => {
    const target = event?.detail?.target;
    if (!(target instanceof Element) || target.id !== "content-wrapper") {
      return;
    }
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
    const target = event?.detail?.target;
    if (!(target instanceof Element) || target.id !== "content-wrapper") {
      return;
    }
    if (pending > 0) {
      pending -= 1;
    }
    if (pending === 0) {
      clearTimer();
      hide();
    }
  };

  document.body.addEventListener("htmx:beforeRequest", start);
  document.body.addEventListener("htmx:afterRequest", end);
  document.body.addEventListener("htmx:sendError", end);
  document.body.addEventListener("htmx:responseError", end);
  document.body.addEventListener("htmx:afterSwap", () => {
    if (pending === 0) {
      hide();
    }
  });

  entriesLoaderRegistered = true;
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

function initProfileClick() {
  const profileBtn = document.getElementById("profile-btn");
  if (!profileBtn || profileBtn.dataset.profileInit === "true") {
    return;
  }

  profileBtn.addEventListener("click", () => {
    const { pathname, search, hash } = window.location;
    sessionStorage.setItem("profile-return", `${pathname}${search}${hash}`);
  });

  profileBtn.dataset.profileInit = "true";
}

export function initGlobalShell() {
  initProfileClick();
  ensureScrollManager();
}

function init() {
  const csrfToken = document.body.dataset.csrfToken || "";
  registerHtmxHeaderHooks(csrfToken);
  registerOfflineHandler();
  initAlertCenter();
  registerEntriesLoader();
  initGlobalShell();
  initGlobalShortcuts();

  updateClientToday();

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
