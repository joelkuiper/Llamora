import { ScrollManager } from "./chat/scroll-manager.js";
import { initGlobalShortcuts } from "./global-shortcuts.js";

const ALERT_AUTO_DISMISS_MS = 6000;
const MAX_ALERTS = 3;
const alertTimers = new WeakMap();
let errorsObserver = null;

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

function stopAutoDismiss(alert) {
  const timer = alertTimers.get(alert);
  if (timer) {
    window.clearTimeout(timer);
    alertTimers.delete(alert);
  }
}

function dismissAlert(alert) {
  stopAutoDismiss(alert);
  if (!alert.isConnected) return;

  alert.classList.add("alert--leaving");
  alert.addEventListener(
    "animationend",
    () => {
      alert.remove();
    },
    { once: true }
  );

  window.setTimeout(() => {
    if (alert.isConnected) {
      alert.remove();
    }
  }, 250);
}

function scheduleAutoDismiss(alert, delay = ALERT_AUTO_DISMISS_MS) {
  stopAutoDismiss(alert);
  const timer = window.setTimeout(() => dismissAlert(alert), delay);
  alertTimers.set(alert, timer);
}

function restartEntranceAnimation(alert) {
  alert.style.animation = "none";
  // Force reflow so the animation restarts reliably across browsers.
  // eslint-disable-next-line no-unused-expressions
  alert.offsetHeight;
  alert.style.animation = "";
}

function initAlert(alert) {
  if (!alert || alert.dataset.alertInit === "true") {
    return;
  }

  const closeBtn = alert.querySelector(".alert__close");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => dismissAlert(alert));
  }

  alert.addEventListener("mouseenter", () => stopAutoDismiss(alert));
  alert.addEventListener("focusin", () => stopAutoDismiss(alert));
  alert.addEventListener("mouseleave", () => {
    if (alert.dataset.autoDismiss === "true") {
      scheduleAutoDismiss(alert);
    }
  });
  alert.addEventListener("focusout", () => {
    if (alert.dataset.autoDismiss === "true") {
      scheduleAutoDismiss(alert);
    }
  });

  alert.dataset.alertInit = "true";
}

function activateAlert(alert, { autoDismiss = false } = {}) {
  if (!alert) return;

  initAlert(alert);
  alert.classList.remove("alert--leaving");
  restartEntranceAnimation(alert);

  if (autoDismiss) {
    alert.dataset.autoDismiss = "true";
    scheduleAutoDismiss(alert);
  } else {
    alert.dataset.autoDismiss = "false";
    stopAutoDismiss(alert);
  }
}

function trimAlertStack(container) {
  const alerts = container.querySelectorAll(".alert");
  if (alerts.length <= MAX_ALERTS) {
    return;
  }

  for (let i = MAX_ALERTS; i < alerts.length; i += 1) {
    const alert = alerts[i];
    if (alert) {
      dismissAlert(alert);
    }
  }
}

function createServerAlert() {
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

  const btn = document.createElement("button");
  btn.className = "alert__close";
  btn.type = "button";
  btn.setAttribute("aria-label", "Dismiss alert");
  btn.textContent = "×";

  alert.append(icon, message, btn);
  return alert;
}

function showServerError() {
  const errors = document.getElementById("errors");
  if (!errors) return;

  const messageText = "Unable to reach server.";
  const existing = errors.querySelector('.alert[data-alert-kind="network"]');
  const alert = existing ?? createServerAlert();

  alert.dataset.alertKind = "network";
  const message = alert.querySelector(".alert__message");
  if (message) {
    message.textContent = messageText;
  }

  if (!existing) {
    errors.prepend(alert);
    trimAlertStack(errors);
  } else {
    errors.prepend(existing);
  }

  activateAlert(alert, { autoDismiss: true });
}

function registerOfflineHandler() {
  if (offlineHandlerRegistered) return;
  document.body.addEventListener("htmx:sendError", showServerError);
  offlineHandlerRegistered = true;
}

function observeErrorContainer() {
  const errors = document.getElementById("errors");
  if (!errors || errors.dataset.observed === "true") {
    return;
  }

  errors.dataset.observed = "true";
  errors.querySelectorAll(".alert").forEach((alert) => activateAlert(alert));

  errorsObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement)) {
          return;
        }

        if (node.classList.contains("alert")) {
          activateAlert(node);
          return;
        }

        const nestedAlerts = node.querySelectorAll?.(".alert");
        if (nestedAlerts) {
          nestedAlerts.forEach((child) => {
            activateAlert(child);
          });
        }
      });
    }
  });

  errorsObserver.observe(errors, { childList: true, subtree: false });
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
  observeErrorContainer();
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
