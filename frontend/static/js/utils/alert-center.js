const ALERT_AUTO_DISMISS_MS = 6000;
const MAX_ALERTS = 3;

const alertTimers = new WeakMap();
const dismissedAlerts = new WeakSet();
const dismissSubscribers = new Set();

let alertContainer = null;
let containerObserver = null;

function stopAutoDismiss(alert) {
  const timer = alertTimers.get(alert);
  if (timer) {
    window.clearTimeout(timer);
    alertTimers.delete(alert);
  }
}

function finalizeDismiss(alert, reason = "removed") {
  if (!alert) {
    return;
  }
  if (dismissedAlerts.has(alert)) {
    return;
  }
  dismissedAlerts.add(alert);
  dismissSubscribers.forEach((listener) => {
    try {
      listener({ alert, reason });
    } catch (error) {
      // Ignore subscriber errors so they do not break dismissal.
    }
  });
}

function dismissAlertElement(alert, reason = "manual") {
  stopAutoDismiss(alert);
  if (!alert || !alert.isConnected) {
    finalizeDismiss(alert, reason);
    return;
  }

  const handleRemoval = () => {
    if (alert.isConnected) {
      alert.remove();
    }
    finalizeDismiss(alert, reason);
  };

  alert.classList.add("alert--leaving");
  alert.addEventListener("animationend", handleRemoval, { once: true });

  window.setTimeout(handleRemoval, 250);
}

function scheduleAutoDismiss(alert, delay = ALERT_AUTO_DISMISS_MS) {
  stopAutoDismiss(alert);
  const timer = window.setTimeout(() => dismissAlertElement(alert, "auto"), delay);
  alertTimers.set(alert, timer);
}

function restartEntranceAnimation(alert) {
  alert.style.animation = "none";
  // Force reflow so the animation restarts reliably across browsers.
  // eslint-disable-next-line no-unused-expressions
  alert.offsetHeight;
  alert.style.animation = "";
}

function applyDataset(alert, dataset = {}) {
  if (!dataset || typeof dataset !== "object") {
    return;
  }
  Object.entries(dataset).forEach(([key, value]) => {
    if (value === null || value === undefined) {
      delete alert.dataset[key];
      return;
    }
    alert.dataset[key] = String(value);
  });
}

function setAlertVariant(alert, variant) {
  const classes = Array.from(alert.classList);
  const extras = classes.filter(
    (name) => !name.startsWith("alert--") && name !== "alert",
  );
  const nextClasses = ["alert"];
  if (variant) {
    nextClasses.push(`alert--${variant}`);
  }
  if (classes.includes("alert--leaving")) {
    nextClasses.push("alert--leaving");
  }
  nextClasses.push(...extras);
  alert.className = nextClasses.join(" ");
  if (variant) {
    alert.dataset.alertVariant = variant;
  } else {
    delete alert.dataset.alertVariant;
  }
}

function applyAlertPayload(alert, payload = {}) {
  if (!alert || typeof payload !== "object") {
    return;
  }

  if (payload.id) {
    alert.dataset.alertId = String(payload.id);
  }
  if (payload.dataset) {
    applyDataset(alert, payload.dataset);
  }
  if (payload.variant) {
    setAlertVariant(alert, payload.variant);
  }
  if (payload.kind) {
    alert.dataset.alertKind = payload.kind;
  }
  if (payload.autoDismiss !== undefined) {
    if (payload.autoDismiss) {
      alert.dataset.autoDismiss = "true";
    } else {
      delete alert.dataset.autoDismiss;
    }
  }

  const message = alert.querySelector(".alert__message");
  if (message) {
    if (payload.html !== undefined && payload.html !== null) {
      message.innerHTML = payload.html;
    } else if (payload.message !== undefined && payload.message !== null) {
      message.textContent = payload.message;
    }
  }

  if (payload.icon !== undefined) {
    let icon = alert.querySelector(".alert__icon");
    if (payload.icon === null) {
      if (icon) {
        icon.remove();
      }
    } else {
      if (!icon) {
        icon = document.createElement("span");
        icon.className = "alert__icon";
        icon.setAttribute("aria-hidden", "true");
        alert.prepend(icon);
      }
      icon.textContent = String(payload.icon);
    }
  }
}

function createAlertElement(payload = {}) {
  const alert = document.createElement("div");
  alert.className = "alert";
  alert.setAttribute("role", "alert");
  alert.setAttribute("aria-atomic", "true");
  const live = payload.ariaLive || (payload.variant === "danger" ? "assertive" : "polite");
  alert.setAttribute("aria-live", live);

  const message = document.createElement("div");
  message.className = "alert__message";
  alert.append(message);

  if (payload.dismissible !== false) {
    const btn = document.createElement("button");
    btn.className = "alert__close";
    btn.type = "button";
    btn.setAttribute("aria-label", "Dismiss alert");
    btn.textContent = "×";
    alert.append(btn);
  }

  applyAlertPayload(alert, payload);
  return alert;
}

function initAlert(alert) {
  if (!alert || alert.dataset.alertInit === "true") {
    return;
  }

  const closeBtn = alert.querySelector(".alert__close");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => dismissAlertElement(alert, "manual"));
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
  if (!container) return;
  const alerts = container.querySelectorAll(".alert");
  if (alerts.length <= MAX_ALERTS) {
    return;
  }

  for (let i = MAX_ALERTS; i < alerts.length; i += 1) {
    const alert = alerts[i];
    if (alert) {
      dismissAlertElement(alert, "overflow");
    }
  }
}

function handleAddedNode(node) {
  if (!(node instanceof HTMLElement)) {
    return;
  }
  if (node.classList.contains("alert")) {
    activateAlert(node, { autoDismiss: node.dataset.autoDismiss === "true" });
    return;
  }
  node.querySelectorAll?.(".alert").forEach((child) => {
    activateAlert(child, { autoDismiss: child.dataset.autoDismiss === "true" });
  });
}

function handleRemovedNode(node) {
  if (!(node instanceof HTMLElement)) {
    return;
  }
  if (node.classList.contains("alert")) {
    finalizeDismiss(node, "removed");
    return;
  }
  node.querySelectorAll?.(".alert").forEach((child) => finalizeDismiss(child, "removed"));
}

function observeContainer(container) {
  if (containerObserver) {
    containerObserver.disconnect();
  }
  containerObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach((node) => handleAddedNode(node));
      mutation.removedNodes.forEach((node) => handleRemovedNode(node));
    }
  });
  containerObserver.observe(container, { childList: true, subtree: false });
}

function ensureContainer() {
  if (alertContainer && alertContainer.isConnected) {
    return alertContainer;
  }
  const fallback = document.getElementById("errors");
  if (fallback) {
    registerAlertContainer(fallback);
  }
  return alertContainer;
}

export function registerAlertContainer(container) {
  if (!container) {
    return null;
  }
  if (alertContainer === container && container.dataset.alertCenter === "true") {
    return alertContainer;
  }

  alertContainer = container;
  alertContainer.dataset.alertCenter = "true";
  alertContainer.querySelectorAll(".alert").forEach((alert) => {
    activateAlert(alert, { autoDismiss: alert.dataset.autoDismiss === "true" });
  });
  trimAlertStack(alertContainer);
  observeContainer(alertContainer);
  return alertContainer;
}

export function getAlertContainer() {
  return ensureContainer();
}

function findExistingAlert(container, payload) {
  if (!payload || !payload.id) {
    return null;
  }
  const id = String(payload.id);
  if (window.CSS?.escape) {
    return container.querySelector(`.alert[data-alert-id="${CSS.escape(id)}"]`);
  }
  return container.querySelector(
    `.alert[data-alert-id="${id.replace(/["\\]/g, "\\$&")}"]`,
  );
}

export function pushAlert(payload) {
  const container = ensureContainer();
  if (!container) {
    return null;
  }

  let alert = null;
  let autoDismiss = false;

  if (payload instanceof HTMLElement) {
    alert = payload;
    autoDismiss = alert.dataset.autoDismiss === "true";
  } else if (payload && typeof payload === "object") {
    alert = findExistingAlert(container, payload);
    if (!alert) {
      alert = createAlertElement(payload);
    } else {
      applyAlertPayload(alert, payload);
    }
    if (payload.autoDismiss !== undefined) {
      autoDismiss = !!payload.autoDismiss;
    } else {
      autoDismiss = alert.dataset.autoDismiss === "true";
    }
  }

  if (!alert) {
    return null;
  }

  container.prepend(alert);
  trimAlertStack(container);
  activateAlert(alert, { autoDismiss });
  return alert;
}

export function onAlertDismiss(listener) {
  if (typeof listener !== "function") {
    return () => {};
  }
  dismissSubscribers.add(listener);
  return () => {
    dismissSubscribers.delete(listener);
  };
}

export function dismissAlert(alert, reason = "manual") {
  dismissAlertElement(alert, reason);
}
