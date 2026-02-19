/**
 * view-state.js — Backwards-compat adapter over app-state.js frame state.
 *
 * Returns the legacy snake_case view-state shape so existing callers
 * (scroll-manager.js, tags-view/index.js) do not need changes.
 * New code should import from app-state.js directly.
 */

import { getFrameState, hydrateFrame } from "./app-state.js";

const toSnakeCase = (frame) => ({
  view: frame.view,
  day: frame.day || null,
  selected_tag: frame.selectedTag || null,
  // sort is preference state — never in frame/view-state
  sort_kind: null,
  sort_dir: null,
  target: frame.target || null,
});

export const hydrateViewState = (root = document) => {
  hydrateFrame(root);
  return toSnakeCase(getFrameState());
};

export const getViewState = () => toSnakeCase(getFrameState());

export const getViewStateValue = (key, fallback = "") => {
  const state = getViewState();
  if (!state || typeof state !== "object") return fallback;
  return state[key] ?? fallback;
};

export default getViewState;
