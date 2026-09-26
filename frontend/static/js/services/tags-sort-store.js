/**
 * tags-sort-store.js — Backwards-compat shim over app-state.js preference state.
 * New code should import getTagsSort / setTagsSort from app-state.js directly.
 */

import {
  getTagsSort,
  normalizeTagsSortDir,
  normalizeTagsSortKind,
  setTagsSort,
} from "./app-state.js";

export { normalizeTagsSortDir, normalizeTagsSortKind, setTagsSort as writeTagsSortState };

export const readTagsSortState = ({ fallbackKind = "count", fallbackDir = "desc" } = {}) => {
  const { sortKind, sortDir } = getTagsSort();
  return {
    sortKind: sortKind || normalizeTagsSortKind(fallbackKind),
    sortDir: sortDir || normalizeTagsSortDir(fallbackDir),
  };
};
