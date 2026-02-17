import { sessionStore } from "../../utils/storage.js";
import { getSelectedTrace } from "./detail.js";
import { findEntriesList } from "./dom.js";
import { getTagsLocationKey } from "./router.js";
import { state } from "./state.js";

const readEntriesAnchorMap = () => sessionStore.get("tags:anchor") ?? {};
const readMainScrollMap = () => sessionStore.get("tags:scroll") ?? {};

const writeEntriesAnchorMap = (map) => {
  sessionStore.set("tags:anchor", map);
};
const writeMainScrollMap = (map) => {
  sessionStore.set("tags:scroll", map);
};

export const getMainScrollElement = () =>
  document.getElementById("main-content") ||
  window.appInit?.scroll?.container ||
  document.getElementById("content-wrapper");

export const scrollMainContentTop = () => {
  const el = getMainScrollElement();
  if (!el) return;
  try {
    el.scrollTo({ top: 0, behavior: "auto" });
  } catch {
    el.scrollTop = 0;
  }
};

const getMainScrollTop = () => {
  const scrollElement = getMainScrollElement();
  if (!(scrollElement instanceof HTMLElement)) return null;
  return Math.max(0, Math.round(scrollElement.scrollTop || 0));
};

export const storeMainScrollTop = () => {
  const key = getTagsLocationKey();
  if (!key) return;
  const top = getMainScrollTop();
  if (!Number.isFinite(top)) return;

  const map = readMainScrollMap();
  map[key] = {
    top,
    updatedAt: Date.now(),
  };
  const entries = Object.entries(map);
  if (entries.length > 120) {
    entries
      .sort((a, b) => Number(b[1]?.updatedAt || 0) - Number(a[1]?.updatedAt || 0))
      .slice(120)
      .forEach(([oldKey]) => {
        delete map[oldKey];
      });
  }
  writeMainScrollMap(map);
};

const readStoredMainScrollTop = () => {
  const key = getTagsLocationKey();
  if (!key) return null;
  const map = readMainScrollMap();
  const value = map[key];
  if (!value || typeof value !== "object") return null;
  const top = Number.parseInt(String(value.top ?? ""), 10);
  if (!Number.isFinite(top)) return null;
  return Math.max(0, top);
};

const applyStoredMainScrollTop = () => {
  const scrollElement = getMainScrollElement();
  if (!(scrollElement instanceof HTMLElement)) return false;
  const top = readStoredMainScrollTop();
  if (!Number.isFinite(top)) return false;
  scrollElement.scrollTop = top;
  return true;
};

const readStoredEntriesAnchor = () => {
  const key = getTagsLocationKey();
  if (!key) return null;
  const map = readEntriesAnchorMap();
  const value = map[key];
  if (!value || typeof value !== "object") return null;
  const entryId = String(value.entryId || "").trim();
  const tag = String(value.tag || "").trim();
  if (!entryId || !tag) return null;
  const offset = Number.parseInt(String(value.offset || "0"), 10);
  return {
    key,
    tag,
    entryId,
    offset: Number.isFinite(offset) ? offset : 0,
  };
};

const storeEntriesAnchor = (payload) => {
  const key = getTagsLocationKey();
  if (!key) return;
  const map = readEntriesAnchorMap();
  map[key] = {
    tag: payload.tag,
    entryId: payload.entryId,
    offset: payload.offset,
    updatedAt: Date.now(),
  };
  const entries = Object.entries(map);
  if (entries.length > 80) {
    entries
      .sort((a, b) => Number(b[1]?.updatedAt || 0) - Number(a[1]?.updatedAt || 0))
      .slice(80)
      .forEach(([oldKey]) => {
        delete map[oldKey];
      });
  }
  writeEntriesAnchorMap(map);
};

export const captureEntriesAnchor = () => {
  const selectedTag = getSelectedTrace();
  if (!selectedTag) return;
  const scrollElement = getMainScrollElement();
  if (!(scrollElement instanceof HTMLElement)) return;
  const entries = findEntriesList();
  if (!(entries instanceof HTMLElement)) return;
  const rows = Array.from(entries.querySelectorAll(".tags-view__entry-item[data-entry-id]"));
  if (!rows.length) return;

  const viewportTop = scrollElement.getBoundingClientRect().top + 8;
  const anchor =
    rows.find((row) => row.getBoundingClientRect().bottom >= viewportTop) || rows[rows.length - 1];
  if (!(anchor instanceof HTMLElement)) return;
  const entryId = String(anchor.dataset.entryId || "").trim();
  if (!entryId) return;
  const offset = Math.round(viewportTop - anchor.getBoundingClientRect().top);
  storeEntriesAnchor({
    tag: selectedTag,
    entryId,
    offset,
  });
};

const scheduleEntriesAnchorSave = () => {
  if (state.saveFrame) return;
  state.saveFrame = window.requestAnimationFrame(() => {
    state.saveFrame = 0;
    if (state.saveSuppressed) return;
    storeMainScrollTop();
    captureEntriesAnchor();
  });
};

export const resetEntriesRestoreState = () => {
  state.restoreAppliedForLocation = "";
};

export const getStoredEntriesAnchor = (tagOverride) => {
  const key = getTagsLocationKey(tagOverride);
  if (!key) return null;
  const map = readEntriesAnchorMap();
  const stored = map[key];
  if (!stored || typeof stored !== "object") return null;
  if (tagOverride) {
    const tag = String(stored.tag || "").trim();
    if (tag && tag !== String(tagOverride || "").trim()) return null;
  }
  return stored;
};

const applyEntriesAnchor = (entryElement, offset) => {
  const scrollElement = getMainScrollElement();
  if (!(scrollElement instanceof HTMLElement)) return false;
  const viewportTop = scrollElement.getBoundingClientRect().top + 8;
  const entryTop = entryElement.getBoundingClientRect().top;
  const desiredTop = viewportTop - offset;
  const delta = entryTop - desiredTop;
  scrollElement.scrollTop += delta;
  return true;
};

const escapeSelectorValue = (value) => {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return String(value).replaceAll('"', '\\"');
};

export const maybeRestoreEntriesAnchor = () => {
  const currentLocation = getTagsLocationKey();
  if (!currentLocation) return;
  if (state.restoreAppliedForLocation === currentLocation) return;
  const params = new URLSearchParams(window.location.search);
  if (params.has("target")) return;
  const selectedTag = getSelectedTrace();
  if (!selectedTag) return;

  applyStoredMainScrollTop();

  const anchor = readStoredEntriesAnchor();
  if (anchor && anchor.tag === selectedTag) {
    const escapedId = escapeSelectorValue(anchor.entryId);
    const entry = document.querySelector(`.tags-view__entry-item[data-entry-id="${escapedId}"]`);
    if (entry instanceof HTMLElement) {
      applyEntriesAnchor(entry, anchor.offset);
      requestAnimationFrame(() => {
        applyEntriesAnchor(entry, anchor.offset);
      });
    }
  }
  state.restoreAppliedForLocation = currentLocation;
};

const isTagsViewActive = () =>
  String(document.getElementById("main-content")?.dataset?.view || "").trim() === "tags";

export const registerTagsScrollStrategy = () => {
  const manager = window.appInit?.scroll;
  if (!manager || typeof manager.registerStrategy !== "function") return;
  manager.registerStrategy("tags-view", {
    matches: () => isTagsViewActive(),
    save: () => {
      scheduleEntriesAnchorSave();
      return true;
    },
    restore: () => {
      maybeRestoreEntriesAnchor();
      return true;
    },
  });
};
