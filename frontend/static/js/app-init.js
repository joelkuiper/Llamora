import { ScrollManager } from "./chat/scroll-manager.js";
import { initGlobalShortcuts } from "./global-shortcuts.js";
import { getActiveDay } from "./chat/active-day-store.js";
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
let resolveAppReady = null;

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

function ensureScrollManager() {
  if (!scrollManager) {
    scrollManager = new ScrollManager();
    scrollManager.start();
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
