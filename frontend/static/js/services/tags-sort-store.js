import { sessionStore } from "../utils/storage.js";

const TAGS_SORT_KEY = "tags:sort";

export const normalizeTagsSortKind = (value, fallback = "count") => {
  const raw = String(value || "")
    .trim()
    .toLowerCase();
  if (raw === "alpha") return "alpha";
  if (raw === "count") return "count";
  return fallback === "alpha" ? "alpha" : "count";
};

export const normalizeTagsSortDir = (value, fallback = "desc") => {
  const raw = String(value || "")
    .trim()
    .toLowerCase();
  if (raw === "asc") return "asc";
  if (raw === "desc") return "desc";
  return fallback === "asc" ? "asc" : "desc";
};

export const readTagsSortState = ({ fallbackKind = "count", fallbackDir = "desc" } = {}) => {
  const raw = sessionStore.get(TAGS_SORT_KEY);
  if (!raw || typeof raw !== "object") {
    return {
      sortKind: normalizeTagsSortKind("", fallbackKind),
      sortDir: normalizeTagsSortDir("", fallbackDir),
    };
  }
  return {
    sortKind: normalizeTagsSortKind(raw.sortKind, fallbackKind),
    sortDir: normalizeTagsSortDir(raw.sortDir, fallbackDir),
  };
};

export const writeTagsSortState = ({ sortKind, sortDir } = {}) => {
  const normalized = {
    sortKind: normalizeTagsSortKind(sortKind),
    sortDir: normalizeTagsSortDir(sortDir),
  };
  const current = readTagsSortState();
  const changed =
    current.sortKind !== normalized.sortKind || current.sortDir !== normalized.sortDir;
  if (!changed) {
    return normalized;
  }
  sessionStore.set(TAGS_SORT_KEY, normalized);
  return normalized;
};
