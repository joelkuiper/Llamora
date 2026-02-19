/**
 * app-state.js — Unified client state registry.
 *
 * Three explicit state categories:
 *
 *  🟢 Frame State  (Navigation / URL-derived)
 *     Source:  server-embedded <script id="view-state"> JSON, replaced on every swap.
 *     Fields:  view, day, selectedTag, target
 *     API:     getFrameState(), hydrateFrame(scope), subscribeFrameState(fn)
 *
 *  🔵 Preference State  (Client-scoped)
 *     Source:  sessionStorage. Survives navigation. Not URL-encoded. Not shareable.
 *     Fields:  tags sort (sortKind + sortDir)
 *     API:     getTagsSort(), setTagsSort({ sortKind, sortDir })
 *              normalizeTagsSortKind(v), normalizeTagsSortDir(v)
 *
 *  🟡 Ephemeral State  (Component-only)
 *     Not managed here. Lives inside component instances. Never persisted or shared.
 */

import { sessionStore } from "../utils/storage.js";

// ─── 🟢 Frame State ──────────────────────────────────────────────────────────

const FRAME_DEFAULTS = Object.freeze({
  view: "diary",
  day: "",
  selectedTag: "",
  target: "",
});

let _frame = { ...FRAME_DEFAULTS };
let _frameRaw = "";
const _frameListeners = new Set();

/** Returns the current frame state (URL-derived, from the server view-state JSON). */
export const getFrameState = () => _frame;

/**
 * Clear the raw-JSON cache so the next hydrateFrame() call re-parses even if
 * the DOM content is byte-for-byte identical to what was last seen.
 *
 * Required before bfcache and history-restore rehydration: the JS module
 * singleton (_frameRaw) persists across page cache save/restore cycles, but
 * the restored HTML may represent a different logical state than what _frame
 * currently holds (e.g. the user navigated away and is now restoring an older
 * snapshot). Without this reset, raw === _frameRaw → early-return → no
 * rehydration → stale frame state → broken view initialization.
 */
export const resetFrameCache = () => {
  _frameRaw = "";
};

/**
 * Subscribe to frame state changes.
 * The callback receives the new frame object and is called synchronously
 * inside hydrateFrame() whenever the state changes.
 * Returns an unsubscribe function.
 */
export const subscribeFrameState = (fn) => {
  _frameListeners.add(fn);
  return () => _frameListeners.delete(fn);
};

/**
 * Read the <script id="view-state"> JSON from `scope` (falling back to document)
 * and update the frame state. Idempotent: skips if raw JSON is unchanged.
 * Returns the current (possibly unchanged) frame state.
 *
 * Called by:
 *  - Module load (initial hydration from server-rendered page)
 *  - lifecycle.js before every app:rehydrate dispatch
 *  - app-init.js registerViewStateHydration (hydration owner + region coalesce)
 */
export const hydrateFrame = (_scope = document) => {
  // Always use document.getElementById — never scope.querySelector.
  //
  // When HTMX does an outerHTML swap, htmx:afterSwap fires with the *old*
  // (now detached) element as e.detail.target. scope.querySelector would find
  // the old #view-state inside the detached element, the raw content would
  // match _frameRaw, and the early-return would prevent the frame from ever
  // updating to the new view. document.getElementById always returns the live
  // in-DOM element, which is the correct post-swap content. OOB view-state
  // updates (hx-swap-oob="outerHTML") also land in the document, so this
  // lookup is always correct regardless of which HTMX swap pattern was used.
  const el = document.getElementById("view-state");

  if (!(el instanceof HTMLScriptElement)) return _frame;

  const raw = el.textContent ?? "";
  if (raw === _frameRaw) return _frame;

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return _frame;
  }
  if (!parsed || typeof parsed !== "object") return _frame;

  const next = {
    view:
      String(parsed.view || "")
        .trim()
        .toLowerCase() || "diary",
    day: String(parsed.day || "").trim(),
    selectedTag: String(parsed.selected_tag || "").trim(),
    target: String(parsed.target || "").trim(),
  };

  // Keep body[data-active-day] in sync.
  // NOTE: body[data-active-day] has TWO write paths that can diverge:
  //   1. Here (frame state from server view-state JSON, set on every full swap)
  //   2. active-day-store.js setActiveDay() (set by entry-view.js after render,
  //      needed because /e/<date> fragment responses do not include view-state JSON)
  // Path 2 is always correct for the diary view. Path 1 is always correct for the
  // tags view. They are the same value in the common case (full navigation).
  if (next.day && document.body?.dataset) {
    document.body.dataset.activeDay = next.day;
  }

  // Sync #main-content[data-view] from the parsed frame state.
  //
  // The server sets this attribute on fresh page loads — but HTMX history restore
  // only replaces the innerHTML of the hx-history-elt (#main-content). It never
  // touches attributes on the element itself. So after a history restore or bfcache
  // restore, data-view can be stale (e.g. "diary" while restored content is "tags"),
  // which breaks every CSS layout rule keyed on #main-content[data-view="tags"].
  // Writing it here makes hydrateFrame the single authoritative reconciler.
  const mainContent = document.getElementById("main-content");
  if (mainContent instanceof HTMLElement && mainContent.dataset.view !== next.view) {
    mainContent.dataset.view = next.view;
  }

  _frameRaw = raw;
  _frame = next;

  document.dispatchEvent(new CustomEvent("app:view-state-changed", { detail: { state: _frame } }));

  for (const fn of _frameListeners) fn(_frame);
  return _frame;
};

// Eagerly hydrate on module load so the first getFrameState() call returns the
// initial server-rendered state without requiring a separate explicit call.
if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => hydrateFrame(document), { once: true });
  } else {
    hydrateFrame(document);
  }
}

// ─── 🔵 Preference State ─────────────────────────────────────────────────────

const SORT_KEY = "tags:sort";

export const normalizeTagsSortKind = (v) =>
  String(v || "").trim() === "alpha" ? "alpha" : "count";

export const normalizeTagsSortDir = (v) => (String(v || "").trim() === "asc" ? "asc" : "desc");

/** Read the persisted tags sort preference from sessionStorage. */
export const getTagsSort = () => {
  const raw = sessionStore.get(SORT_KEY);
  return {
    sortKind: normalizeTagsSortKind(raw?.sortKind),
    sortDir: normalizeTagsSortDir(raw?.sortDir),
  };
};

/**
 * Write the tags sort preference to sessionStorage.
 * Skips the write if the normalized value is unchanged.
 * Returns the normalized value.
 */
export const setTagsSort = ({ sortKind, sortDir } = {}) => {
  const next = {
    sortKind: normalizeTagsSortKind(sortKind),
    sortDir: normalizeTagsSortDir(sortDir),
  };
  const current = getTagsSort();
  if (current.sortKind !== next.sortKind || current.sortDir !== next.sortDir) {
    sessionStore.set(SORT_KEY, next);
  }
  return next;
};
