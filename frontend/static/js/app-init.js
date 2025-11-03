import { ScrollManager } from "./chat/scroll-manager.js";
import { initGlobalShortcuts } from "./global-shortcuts.js";

let headersRegistered = false;
let offlineHandlerRegistered = false;
let scrollManager = null;

function registerHtmxHeaderHooks(csrfToken) {
  if (headersRegistered) return;
  document.body.addEventListener("htmx:configRequest", (event) => {
    const headers = event.detail?.headers;
    if (!headers) return;

    if (csrfToken) {
      headers["X-CSRFToken"] = csrfToken;
    }

    const activeDate = document.body?.dataset?.activeDay;
    if (activeDate) {
      headers["X-Active-Day"] = activeDate;
    }
  });
  headersRegistered = true;
}

function showServerError() {
  const errors = document.getElementById("errors");
  if (!errors) return;

  const alert = document.createElement("div");
  alert.className = "alert alert--danger";
  alert.setAttribute("role", "alert");
  alert.setAttribute("aria-live", "assertive");
  alert.setAttribute("aria-atomic", "true");

  const icon = document.createElement("span");
  icon.className = "alert__icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "⚠️";

  const message = document.createElement("div");
  message.className = "alert__message";
  message.textContent = "Unable to reach server.";

  const btn = document.createElement("button");
  btn.className = "alert__close";
  btn.type = "button";
  btn.setAttribute("aria-label", "Dismiss alert");
  btn.textContent = "×";
  btn.addEventListener("click", () => alert.remove());

  alert.append(icon, message, btn);
  errors.appendChild(alert);
}

function registerOfflineHandler() {
  if (offlineHandlerRegistered) return;
  document.body.addEventListener("htmx:sendError", showServerError);
  offlineHandlerRegistered = true;
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
  initGlobalShell();
  initGlobalShortcuts();

  window.appInit = {
    ...(window.appInit || {}),
    initGlobalShell,
    scroll: ensureScrollManager(),
  };
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}
