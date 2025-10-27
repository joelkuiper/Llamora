import { initScrollMemory } from "./scroll.js";

let headersRegistered = false;
let offlineHandlerRegistered = false;
let scrollInitialized = false;

function registerHtmxHeaderHooks(csrfToken) {
  if (headersRegistered) return;
  document.body.addEventListener("htmx:configRequest", (event) => {
    const headers = event.detail?.headers;
    if (!headers) return;

    if (csrfToken) {
      headers["X-CSRFToken"] = csrfToken;
    }

    const chat = document.getElementById("chat");
    const activeDate = chat?.dataset?.date;
    if (activeDate) {
      headers["X-Active-Day"] = activeDate;
    }
  });
  headersRegistered = true;
}

function showServerError() {
  const errors = document.getElementById("errors");
  if (!errors) return;

  const box = document.createElement("div");
  box.className = "error-box";
  box.textContent = "⚠️ Unable to reach server.";

  const btn = document.createElement("button");
  btn.className = "close-error";
  btn.setAttribute("aria-label", "Close error");
  btn.textContent = "×";
  btn.addEventListener("click", () => box.remove());

  box.appendChild(btn);
  errors.appendChild(box);
}

function registerOfflineHandler() {
  if (offlineHandlerRegistered) return;
  document.body.addEventListener("htmx:sendError", showServerError);
  offlineHandlerRegistered = true;
}

function ensureScrollMemory() {
  if (scrollInitialized) return;
  initScrollMemory();
  scrollInitialized = true;
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
  ensureScrollMemory();
}

function init() {
  const csrfToken = document.body.dataset.csrfToken || "";
  registerHtmxHeaderHooks(csrfToken);
  registerOfflineHandler();
  initGlobalShell();

  window.appInit = {
    ...(window.appInit || {}),
    initGlobalShell,
  };
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}
