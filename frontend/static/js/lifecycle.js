let currentView = null;
let initialized = false;

function getView() {
  return document.getElementById("main-content")?.dataset?.view || "diary";
}

function dispatch(name, detail = {}) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

export function rehydrate(detail = {}) {
  const view = getView();
  if (view !== currentView) {
    const prev = currentView;
    currentView = view;
    dispatch("app:view-changed", { view, previousView: prev });
  }
  dispatch("app:rehydrate", { reason: "init", ...detail });
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

  // bfcache
  window.addEventListener("pageshow", (e) => {
    if (!e.persisted) return;
    currentView = getView();
    dispatch("app:rehydrate", { reason: "bfcache" });
  });
  window.addEventListener("pagehide", (e) => {
    if (e.persisted) {
      dispatch("app:teardown", { reason: "bfcache" });
    }
  });

  // htmx history
  document.body.addEventListener("htmx:beforeHistorySave", () => {
    dispatch("app:teardown", { reason: "history-save" });
  });
  document.body.addEventListener("htmx:historyRestore", () => {
    currentView = getView();
    dispatch("app:rehydrate", { reason: "history-restore" });
  });

  // htmx swaps targeting major content areas
  const rehydrateTargets = new Set(["content-wrapper", "main-content", "profile-modal-root"]);
  document.body.addEventListener("htmx:afterSwap", (e) => {
    const target = e.detail?.target;
    if (!target) return;
    dispatch("app:region-swapped", {
      reason: "htmx-after-swap",
      target,
      id: target.id || null,
    });
    const id = target.id;
    if (!rehydrateTargets.has(id)) return;

    if (id === "main-content") {
      const newView = getView();
      if (newView !== currentView) {
        const prev = currentView;
        currentView = newView;
        dispatch("app:view-changed", { view: newView, previousView: prev });
      }
    }

    dispatch("app:rehydrate", { reason: "swap", target });
  });

  document.body.addEventListener("htmx:afterSettle", (e) => {
    const target = e.detail?.target;
    if (!target) return;
    dispatch("app:region-settled", {
      reason: "htmx-after-settle",
      target,
      id: target.id || null,
    });
  });

  // visibility
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      dispatch("app:rehydrate", { reason: "visibility" });
    }
  });
}
