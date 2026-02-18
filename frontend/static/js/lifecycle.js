let currentView = null;
let initialized = false;
let rehydrateCycle = 0;
let teardownCycle = 0;

function getView() {
  return document.getElementById("main-content")?.dataset?.view || "diary";
}

function dispatch(name, detail = {}) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

function dispatchRehydrate(detail = {}) {
  rehydrateCycle += 1;
  dispatch("app:rehydrate", { cycle: rehydrateCycle, ...detail });
}

function dispatchTeardown(detail = {}) {
  teardownCycle += 1;
  dispatch("app:teardown", { cycle: teardownCycle, ...detail });
}

export function rehydrate(detail = {}) {
  const view = getView();
  if (view !== currentView) {
    const prev = currentView;
    currentView = view;
    dispatch("app:view-changed", { view, previousView: prev });
  }
  dispatchRehydrate({ reason: "init", ...detail });
}

export function teardown(detail = {}) {
  dispatchTeardown(detail);
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
    dispatchRehydrate({ reason: "bfcache" });
  });
  window.addEventListener("pagehide", (e) => {
    if (e.persisted) {
      dispatchTeardown({ reason: "bfcache" });
    }
  });

  // htmx history
  document.body.addEventListener("htmx:beforeHistorySave", () => {
    dispatchTeardown({ reason: "history-save" });
  });
  document.body.addEventListener("htmx:historyRestore", () => {
    currentView = getView();
    dispatchRehydrate({ reason: "history-restore" });
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

    dispatchRehydrate({ reason: "swap", target, context: target });
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
      dispatchRehydrate({ reason: "visibility" });
    }
  });
}
