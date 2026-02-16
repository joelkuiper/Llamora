import { ingestSourceEvent, requestRehydrate } from "./runtime/rehydration-coordinator.js";

let currentView = null;
let initialized = false;

function getView() {
  return document.getElementById("main-content")?.dataset?.view || "diary";
}

function dispatch(name, detail = {}) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

function updateViewChanged() {
  const view = getView();
  if (view === currentView) {
    return;
  }
  const previousView = currentView;
  currentView = view;
  dispatch("app:view-changed", { view, previousView });
}

export function teardown(detail = {}) {
  dispatch("app:teardown", detail);
}

export function getCurrentView() {
  return currentView;
}

export function init() {
  if (initialized) return;
  initialized = true;

  currentView = getView();
  requestRehydrate({ reason: "init", regionId: "document" });

  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    updateViewChanged();
    requestRehydrate({ reason: "bfcache", regionId: "document" });
  });

  window.addEventListener("pagehide", (event) => {
    if (event.persisted) {
      dispatch("app:teardown", { reason: "bfcache" });
    }
  });

  document.body.addEventListener("htmx:beforeHistorySave", () => {
    dispatch("app:teardown", { reason: "history-save" });
  });

  document.body.addEventListener("htmx:historyRestore", () => {
    updateViewChanged();
    requestRehydrate({ reason: "history-restore", regionId: "document" });
  });

  const rehydrateTargets = new Set(["content-wrapper", "main-content", "profile-modal-root"]);
  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail?.target;
    if (!target) return;
    const regionId = target.id || null;

    dispatch("app:region-swapped", {
      reason: "htmx-after-swap",
      target,
      id: regionId,
    });

    if (!rehydrateTargets.has(regionId)) return;

    if (regionId === "main-content") {
      updateViewChanged();
    }

    requestRehydrate({ reason: "swap", regionId });
  });

  document.body.addEventListener("htmx:afterSettle", (event) => {
    const target = event.detail?.target;
    if (!target) return;
    dispatch("app:region-settled", {
      reason: "htmx-after-settle",
      target,
      id: target.id || null,
    });
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      requestRehydrate({ reason: "visibility", regionId: "document" });
    }
  });

  if (!globalThis.appRuntime) {
    globalThis.appRuntime = {};
  }

  globalThis.appRuntime.ingestRehydrateSource = (event) => {
    ingestSourceEvent(event, { reason: "htmx-after-settle" });
  };
}
