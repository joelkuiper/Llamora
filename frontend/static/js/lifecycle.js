import { getFrameState, hydrateFrame, resetFrameCache } from "./services/app-state.js";

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

/**
 * Hydrates frame state from `scope` then dispatches app:rehydrate.
 * The `frame` field in the event detail reflects the freshly hydrated state,
 * so component handlers can read it directly without importing app-state.js.
 */
function dispatchRehydrate(detail = {}) {
  const scope = detail.context || detail.target || document;
  hydrateFrame(scope);
  rehydrateCycle += 1;
  dispatch("app:rehydrate", { cycle: rehydrateCycle, frame: getFrameState(), ...detail });
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
    // Reset the raw-JSON cache so hydrateFrame() re-parses even if the restored
    // DOM content is identical to what _frameRaw last held. The JS module
    // singleton persists across bfcache save/restore; without this reset, the
    // raw === _frameRaw early-return prevents the frame from re-hydrating to the
    // restored page's actual view state.
    resetFrameCache();
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
    // Same reason as bfcache: restored HTML may represent a different view than
    // the current module-level _frameRaw; reset so hydrateFrame() re-parses.
    resetFrameCache();
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
