import * as FuseModule from "fuse.js";
import { armEntryAnimations, armInitialEntryAnimations } from "../entries/entry-animations.js";
import { formatTimeElements } from "../services/time.js";
import { clearScrollTarget, flashHighlight } from "../ui.js";
import { sessionStore } from "../utils/storage.js";

const FuseCtor = FuseModule.default ?? FuseModule;

const BOOT_KEY = "__llamoraTagsViewBooted";
const ENTRIES_LOAD_RESTORE_LIMIT = 48;

const state = {
  query: "",
  sortKind: "count",
  sortDir: "desc",
  fuse: null,
  rows: [],
  input: null,
  clearBtn: null,
  empty: null,
  list: null,
  scrollElement: null,
  saveFrame: 0,
  restoreToken: 0,
  restoreInFlight: false,
  restoreAppliedForLocation: "",
};

const readStoredSearchQuery = () => sessionStore.get("tags:query") ?? "";

const persistSearchQuery = (value) => {
  if (value) {
    sessionStore.set("tags:query", value);
  } else {
    sessionStore.delete("tags:query");
  }
};

const setClearButtonVisibility = () => {
  if (!(state.clearBtn instanceof HTMLButtonElement)) return;
  const hasQuery = Boolean(String(state.query || "").trim());
  state.clearBtn.classList.toggle("is-visible", hasQuery);
  state.clearBtn.setAttribute("aria-hidden", hasQuery ? "false" : "true");
  state.clearBtn.tabIndex = hasQuery ? 0 : -1;
};

const readEntriesAnchorMap = () => sessionStore.get("tags:anchor") ?? {};

const writeEntriesAnchorMap = (map) => {
  sessionStore.set("tags:anchor", map);
};

const findList = (root = document) =>
  root.querySelector?.("#tags-view-list") || document.getElementById("tags-view-list");

const findDetail = (root = document) =>
  root.querySelector?.("#tags-view-detail") || document.getElementById("tags-view-detail");

const findSidebar = (root = document) =>
  root.querySelector?.(".tags-view__sidebar-fixed") ||
  document.querySelector(".tags-view__sidebar-fixed");

const findListBody = (root = document) =>
  root.querySelector?.(".tags-view__list-body") || document.querySelector(".tags-view__list-body");

const findEntriesList = (root = document) =>
  findDetail(root)?.querySelector?.("[data-tags-view-entries]") ||
  document.querySelector("#tags-view-detail [data-tags-view-entries]");

const getMainScrollElement = () =>
  document.getElementById("main-content") ||
  window.appInit?.scroll?.container ||
  document.getElementById("content-wrapper");

const scrollMainContentTop = () => {
  const el = getMainScrollElement();
  if (!el) return;
  try {
    el.scrollTo({ top: 0, behavior: "auto" });
  } catch {
    el.scrollTop = 0;
  }
};

const getTagsLocationKey = () => {
  const url = new URL(window.location.href);
  if (url.searchParams.get("view") !== "tags") return "";
  url.searchParams.delete("target");
  return `${url.pathname}?${url.searchParams.toString()}`;
};

const getSelectedTrace = (root = document) =>
  String(findDetail(root)?.dataset?.selectedTag || "").trim();

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
    offset: Number.isFinite(offset) ? Math.max(0, offset) : 0,
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

const captureEntriesAnchor = () => {
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
  const offset = Math.max(0, Math.round(viewportTop - anchor.getBoundingClientRect().top));
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
    if (state.restoreInFlight) return;
    captureEntriesAnchor();
  });
};

const resetEntriesRestoreState = () => {
  state.restoreToken += 1;
  state.restoreInFlight = false;
  state.restoreAppliedForLocation = "";
};

const shouldForceRestoreCycle = (reason) => reason === "bfcache" || reason === "history-restore";

const applyEntriesAnchor = (entryElement, offset) => {
  const scrollElement = getMainScrollElement();
  if (!(scrollElement instanceof HTMLElement)) return false;
  const viewportTop = scrollElement.getBoundingClientRect().top + 8;
  const entryTop = entryElement.getBoundingClientRect().top;
  const desiredTop = viewportTop - Math.max(0, offset);
  const delta = entryTop - desiredTop;
  scrollElement.scrollTop += delta;
  return true;
};

const requestEntriesChunk = (sentinel, token) =>
  new Promise((resolve) => {
    const url = sentinel.getAttribute("hx-get");
    if (!url || typeof htmx === "undefined" || typeof htmx.ajax !== "function") {
      resolve(false);
      return;
    }

    let done = false;
    const cleanup = () => {
      document.body.removeEventListener("htmx:afterSwap", onAfterSwap);
      document.body.removeEventListener("htmx:responseError", onResponseError);
      window.clearTimeout(timeoutId);
    };
    const finish = (value) => {
      if (done) return;
      done = true;
      cleanup();
      resolve(value);
    };
    const onAfterSwap = (event) => {
      if (token !== state.restoreToken) {
        finish(false);
        return;
      }
      const source = event.detail?.requestConfig?.elt;
      if (source !== sentinel) return;
      finish(true);
    };
    const onResponseError = (event) => {
      const source = event.detail?.requestConfig?.elt;
      if (source !== sentinel) return;
      finish(false);
    };

    const timeoutId = window.setTimeout(() => finish(false), 3000);
    document.body.addEventListener("htmx:afterSwap", onAfterSwap);
    document.body.addEventListener("htmx:responseError", onResponseError);

    htmx.ajax("GET", url, {
      source: sentinel,
      target: sentinel,
      swap: "outerHTML",
    });
  });

const escapeSelectorValue = (value) => {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return String(value).replaceAll('"', '\\"');
};

const ensureEntriesAnchorVisible = async (anchor, token) => {
  const escapedId = escapeSelectorValue(anchor.entryId);
  for (let i = 0; i <= ENTRIES_LOAD_RESTORE_LIMIT; i += 1) {
    if (token !== state.restoreToken) return null;
    const entry = document.querySelector(`.tags-view__entry-item[data-entry-id="${escapedId}"]`);
    if (entry instanceof HTMLElement) {
      return entry;
    }
    const sentinel = document.querySelector(".tags-view__entries .tags-view__entries-load-more");
    if (!(sentinel instanceof HTMLElement)) {
      return null;
    }
    const loaded = await requestEntriesChunk(sentinel, token);
    if (!loaded) {
      return null;
    }
  }
  return null;
};

const maybeRestoreEntriesAnchor = async () => {
  const currentLocation = getTagsLocationKey();
  if (!currentLocation) return;
  if (state.restoreAppliedForLocation === currentLocation) return;
  if (state.restoreInFlight) return;
  const params = new URLSearchParams(window.location.search);
  if (params.has("target")) return;
  const selectedTag = getSelectedTrace();
  if (!selectedTag) return;

  const anchor = readStoredEntriesAnchor();
  if (!anchor || anchor.key !== currentLocation || anchor.tag !== selectedTag) {
    state.restoreAppliedForLocation = currentLocation;
    return;
  }

  state.restoreInFlight = true;
  state.restoreToken += 1;
  const token = state.restoreToken;
  try {
    const entry = await ensureEntriesAnchorVisible(anchor, token);
    if (!(entry instanceof HTMLElement)) {
      state.restoreAppliedForLocation = currentLocation;
      return;
    }
    applyEntriesAnchor(entry, anchor.offset);
    state.restoreAppliedForLocation = currentLocation;
    scheduleEntriesAnchorSave();
  } finally {
    if (token === state.restoreToken) {
      state.restoreInFlight = false;
    }
  }
};

const attachEntriesScrollListener = () => {
  const next = getMainScrollElement();
  if (state.scrollElement === next) return;
  if (state.scrollElement instanceof HTMLElement) {
    state.scrollElement.removeEventListener("scroll", scheduleEntriesAnchorSave);
  }
  state.scrollElement = next instanceof HTMLElement ? next : null;
  if (state.scrollElement) {
    state.scrollElement.addEventListener("scroll", scheduleEntriesAnchorSave, { passive: true });
  }
};

const updateHeaderHeight = () => {
  const header = document.getElementById("app-header");
  if (!header) return;
  const height = Math.ceil(header.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--app-header-height", `${height}px`);
};

const normalizeSortKind = (value) =>
  String(value || "")
    .trim()
    .toLowerCase() === "count"
    ? "count"
    : "alpha";

const normalizeSortDir = (value) =>
  String(value || "")
    .trim()
    .toLowerCase() === "desc"
    ? "desc"
    : "asc";

const setActiveTag = (tagName, root = document) => {
  const list = findList(root);
  if (!list) return;
  const targetName = String(tagName || "").trim();
  let activeRow = null;
  list.querySelectorAll(".tags-view__index-row").forEach((row) => {
    const isActive = targetName !== "" && row.dataset.tagName === targetName;
    row.classList.toggle("is-active", isActive);
    if (isActive) {
      row.setAttribute("aria-current", "true");
      activeRow = row;
    } else {
      row.removeAttribute("aria-current");
    }
  });
  if (!(activeRow instanceof HTMLElement)) return;
  const listBody = findListBody(root);
  if (listBody instanceof HTMLElement) {
    const bodyRect = listBody.getBoundingClientRect();
    const rowRect = activeRow.getBoundingClientRect();
    const outsideTop = rowRect.top < bodyRect.top + 6;
    const outsideBottom = rowRect.bottom > bodyRect.bottom - 6;
    if (outsideTop || outsideBottom) {
      activeRow.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    }
    return;
  }
  activeRow.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
};

const readSortFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  const hasKind = params.has("sort_kind");
  const hasDir = params.has("sort_dir");
  return {
    kind: normalizeSortKind(params.get("sort_kind")),
    dir: normalizeSortDir(params.get("sort_dir")),
    hasKind,
    hasDir,
  };
};

const syncFromDetail = (root = document) => {
  const detail = findDetail(root);
  const urlSort = readSortFromUrl();
  if (detail) {
    state.sortKind = urlSort.hasKind ? urlSort.kind : normalizeSortKind(detail.dataset.sortKind);
    state.sortDir = urlSort.hasDir ? urlSort.dir : normalizeSortDir(detail.dataset.sortDir);
    setActiveTag(detail.dataset.selectedTag || "", root);
  } else {
    state.sortKind = urlSort.kind;
    state.sortDir = urlSort.dir;
  }
  updateSortButtons(root);
};

const updateSortButtons = (root = document) => {
  const sidebar = findSidebar(root);
  if (!sidebar) return;
  sidebar.querySelectorAll("[data-tags-sort-kind][data-tags-sort-dir]").forEach((button) => {
    const isActive =
      normalizeSortKind(button.dataset.tagsSortKind) === state.sortKind &&
      normalizeSortDir(button.dataset.tagsSortDir) === state.sortDir;
    button.classList.toggle("is-active", isActive);
  });
};

const updateUrlWithSort = (rawUrl, { clearTarget = false } = {}) => {
  const current = new URL(window.location.href);
  const next = new URL(rawUrl, current.origin);
  next.searchParams.set("sort_kind", state.sortKind);
  next.searchParams.set("sort_dir", state.sortDir);
  if (clearTarget) {
    next.searchParams.delete("target");
  }
  return `${next.pathname}${next.search}${next.hash}`;
};

const ensureSortParams = (element) => {
  if (!(element instanceof Element)) return;
  const href = element.getAttribute("href");
  const hxGet = element.getAttribute("hx-get");
  const hxPush = element.getAttribute("hx-push-url");
  const clearTarget = element.classList.contains("tags-view__index-row");
  if (href) element.setAttribute("href", updateUrlWithSort(href, { clearTarget }));
  if (hxGet) element.setAttribute("hx-get", updateUrlWithSort(hxGet, { clearTarget }));
  if (hxPush) element.setAttribute("hx-push-url", updateUrlWithSort(hxPush, { clearTarget }));
};

const syncSortLinks = () => {
  const list = findList();
  if (!list) return;
  list.querySelectorAll(".tags-view__index-row").forEach((row) => {
    ensureSortParams(row);
  });

  const detail = findDetail();
  if (!detail) return;
  detail.querySelectorAll(".tags-view__related-link, .tags-view__entry-tag").forEach((link) => {
    if (!(link instanceof HTMLAnchorElement)) return;
    ensureSortParams(link);
  });
};

const animateDetailEntries = (root = document) => {
  const detail = findDetail(root);
  if (!detail) return;
  formatTimeElements(detail);
  armEntryAnimations(detail);
  const entries = detail.querySelector(".tags-view__entries");
  if (!(entries instanceof HTMLElement)) return;
  armInitialEntryAnimations(entries);
};

const highlightRequestedTag = (root = document) => {
  const params = new URLSearchParams(window.location.search);
  const target = String(params.get("target") || "").trim();
  if (!target || !target.startsWith("tag-index-")) return;
  const row = document.getElementById(target);
  if (row instanceof HTMLElement) {
    const tagName = row.dataset.tagName || "";
    if (tagName) {
      setActiveTag(tagName, root);
    }
    flashHighlight(row);
  }
  clearScrollTarget(target, { emitEvent: false });
};

const shouldResetSearchForTargetNavigation = () => {
  const params = new URLSearchParams(window.location.search);
  const target = String(params.get("target") || "").trim();
  return target.startsWith("tag-index-");
};

const clearSearchForTargetNavigation = () => {
  if (!shouldResetSearchForTargetNavigation()) return;
  if (!state.query) return;
  state.query = "";
  persistSearchQuery("");
  if (state.input) {
    state.input.value = "";
  }
};

const sortRows = () => {
  if (!state.list || state.rows.length <= 1) return;

  const sorted = [...state.rows].sort((a, b) => {
    const nameA = (a.dataset.tagsName || "").toLowerCase();
    const nameB = (b.dataset.tagsName || "").toLowerCase();
    const countA = Number.parseInt(a.dataset.tagsCount || "0", 10) || 0;
    const countB = Number.parseInt(b.dataset.tagsCount || "0", 10) || 0;

    if (state.sortKind === "count") {
      if (countA !== countB) {
        return state.sortDir === "desc" ? countB - countA : countA - countB;
      }
      return nameA.localeCompare(nameB);
    }

    const alphaCmp = nameA.localeCompare(nameB);
    return state.sortDir === "desc" ? -alphaCmp : alphaCmp;
  });

  sorted.forEach((row) => {
    state.list.appendChild(row);
  });
  state.rows = sorted;
};

const buildSearchIndex = (root = document) => {
  const list = findList(root) || document.querySelector("#tags-view-list");
  const sidebar = findSidebar(root);
  state.list = list?.querySelector("[data-tags-view-index]") || null;
  state.rows = Array.from(state.list?.querySelectorAll(".tags-view__index-row") || []);
  state.input = sidebar?.querySelector("[data-tags-view-search]") || null;
  state.clearBtn = sidebar?.querySelector("[data-tags-view-search-clear]") || null;
  state.empty = list?.querySelector("[data-tags-view-empty]") || null;

  const searchable = state.rows.map((row) => ({
    name: row.dataset.tagsName || "",
    count: Number.parseInt(row.dataset.tagsCount || "0", 10) || 0,
    row,
  }));

  if (typeof FuseCtor === "function") {
    try {
      state.fuse = new FuseCtor(searchable, {
        keys: ["name"],
        threshold: 0.34,
        ignoreLocation: true,
        minMatchCharLength: 1,
      });
    } catch (error) {
      console.warn("[tags-view] Fuse init failed, falling back to plain search.", error);
      state.fuse = null;
    }
  } else {
    state.fuse = null;
  }

  state.rows.forEach((row, index) => {
    const nameEl = row.querySelector(".tags-view__index-name");
    if (nameEl instanceof HTMLElement) {
      nameEl.dataset.originalText = row.dataset.tagsName || nameEl.textContent || "";
    }
    row.dataset.tagsIndex = String(index);
  });

  if (state.input) {
    state.input.value = state.query;
  }
  setClearButtonVisibility();
};

const escapeHtml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const highlightMatch = (text, query) => {
  if (!query) return escapeHtml(text);
  const lowerText = text.toLowerCase();
  const lowerQuery = query.toLowerCase();
  const idx = lowerText.indexOf(lowerQuery);
  if (idx < 0) return escapeHtml(text);
  const before = escapeHtml(text.slice(0, idx));
  const match = escapeHtml(text.slice(idx, idx + query.length));
  const after = escapeHtml(text.slice(idx + query.length));
  return `${before}<mark>${match}</mark>${after}`;
};

const applySearch = (rawQuery) => {
  if (!state.rows.length) {
    buildSearchIndex();
  }
  const query = String(rawQuery || "").trim();
  state.query = query;
  persistSearchQuery(query);
  setClearButtonVisibility();
  if (!state.rows.length) return;

  if (!query) {
    state.rows.forEach((row) => {
      row.hidden = false;
      row.classList.remove("is-filtered-out");
      row.toggleAttribute("hidden", false);
      row.style.display = "";
      const nameEl = row.querySelector(".tags-view__index-name");
      if (nameEl instanceof HTMLElement) {
        const original = nameEl.dataset.originalText || row.dataset.tagsName || "";
        nameEl.textContent = original;
      }
    });
    sortRows();
    if (state.empty) {
      state.empty.hidden = true;
    }
    return;
  }

  let matches = new Set();
  let orderedMatches = [];
  if (state.fuse) {
    const results = state.fuse.search(query) || [];
    orderedMatches = results.map((item) => item.item.row);
    matches = new Set(orderedMatches);
  } else {
    const normalized = query.toLowerCase();
    state.rows.forEach((row) => {
      const name = (row.dataset.tagsName || "").toLowerCase();
      if (name.includes(normalized)) {
        matches.add(row);
      }
    });
    orderedMatches = state.rows
      .filter((row) => matches.has(row))
      .sort((a, b) => {
        const aName = (a.dataset.tagsName || "").toLowerCase();
        const bName = (b.dataset.tagsName || "").toLowerCase();
        const aIndex = aName.indexOf(normalized);
        const bIndex = bName.indexOf(normalized);
        if (aIndex !== bIndex) {
          return aIndex - bIndex;
        }
        return aName.localeCompare(bName);
      });
  }
  if (state.list) {
    const remainder = state.rows.filter((row) => !matches.has(row));
    [...orderedMatches, ...remainder].forEach((row) => {
      state.list.appendChild(row);
    });
  }
  let visibleCount = 0;
  state.rows.forEach((row) => {
    const isVisible = matches.has(row);
    row.hidden = !isVisible;
    row.classList.toggle("is-filtered-out", !isVisible);
    row.toggleAttribute("hidden", !isVisible);
    row.style.display = isVisible ? "" : "none";
    if (isVisible) {
      const nameEl = row.querySelector(".tags-view__index-name");
      if (nameEl instanceof HTMLElement) {
        const original = nameEl.dataset.originalText || row.dataset.tagsName || "";
        nameEl.innerHTML = highlightMatch(original, query);
      }
    }
    if (isVisible) visibleCount += 1;
  });
  if (state.empty) {
    state.empty.hidden = visibleCount > 0;
  }
};

const updateUrlSort = () => {
  const detail = findDetail();
  const selectedTag = detail?.dataset?.selectedTag || "";
  const url = new URL(window.location.href);
  url.searchParams.set("view", "tags");
  url.searchParams.set("sort_kind", state.sortKind);
  url.searchParams.set("sort_dir", state.sortDir);
  if (selectedTag) {
    url.searchParams.set("tag", selectedTag);
  } else {
    url.searchParams.delete("tag");
  }
  url.searchParams.delete("target");
  window.history.replaceState(window.history.state, "", url.toString());
};

const applySort = (kind, dir) => {
  state.sortKind = normalizeSortKind(kind);
  state.sortDir = normalizeSortDir(dir);
  const detail = findDetail();
  if (detail) {
    detail.dataset.sortKind = state.sortKind;
    detail.dataset.sortDir = state.sortDir;
  }
  syncSortLinks();
  sortRows();
  updateSortButtons();
  applySearch(state.query);
  updateUrlSort();
};

const sync = (root = document) => {
  const hadTargetParam = new URLSearchParams(window.location.search).has("target");
  updateHeaderHeight();
  attachEntriesScrollListener();
  if (!state.query) {
    state.query = readStoredSearchQuery();
  }
  syncFromDetail(root);
  buildSearchIndex(root);
  syncSortLinks();
  sortRows();
  clearSearchForTargetNavigation();
  applySearch(state.query);
  syncFromDetail(root);
  animateDetailEntries(root);
  highlightRequestedTag(root);
  if (!hadTargetParam) {
    void maybeRestoreEntriesAnchor();
  }
};

if (!globalThis[BOOT_KEY]) {
  globalThis[BOOT_KEY] = true;
  window.addEventListener("resize", updateHeaderHeight);

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const clearBtn = target.closest("[data-tags-view-search-clear]");
    if (clearBtn instanceof HTMLButtonElement) {
      event.preventDefault();
      applySearch("");
      if (state.input instanceof HTMLInputElement) {
        state.input.value = "";
        state.input.focus();
      }
      return;
    }

    const entryLink = target.closest(
      "#tags-view-detail .tags-view__entry-open, #tags-view-detail .tags-view__entry-date",
    );
    if (entryLink) {
      captureEntriesAnchor();
      return;
    }

    const sortButton = target.closest("[data-tags-sort-kind][data-tags-sort-dir]");
    if (sortButton instanceof HTMLButtonElement) {
      event.preventDefault();
      applySort(sortButton.dataset.tagsSortKind, sortButton.dataset.tagsSortDir);
      return;
    }

    const row = target.closest("#tags-view-list .tags-view__index-row");
    if (row) {
      ensureSortParams(row);
      const tagName = row.dataset.tagName || "";
      if (!tagName) return;
      clearScrollTarget(null, { emitEvent: false });
      setActiveTag(tagName);
      scrollMainContentTop();
      return;
    }

    const detailLink = target.closest(
      "#tags-view-detail .tags-view__related-link, #tags-view-detail .tags-view__entry-tag",
    );
    if (!(detailLink instanceof HTMLAnchorElement)) return;
    ensureSortParams(detailLink);
    const tagName = (detailLink.textContent || "").trim();
    if (tagName) {
      setActiveTag(tagName);
      const linkedRow = document.getElementById(`tag-index-${tagName}`);
      if (linkedRow instanceof HTMLElement) {
        flashHighlight(linkedRow);
      }
    }
    scrollMainContentTop();
  });

  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.matches("[data-tags-view-search]")) return;
    applySearch(target.value);
  });

  document.addEventListener("keyup", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.matches("[data-tags-view-search]")) return;
    applySearch(target.value);
  });

  document.addEventListener("search", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.matches("[data-tags-view-search]")) return;
    applySearch(target.value);
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    const inList = target.closest?.("#tags-view-list");
    const inEntries = target.closest?.("[data-tags-view-entries]");
    if (
      target.id === "tags-view-detail" ||
      target.id === "main-content" ||
      target.id === "tags-view-list" ||
      inList ||
      inEntries
    ) {
      sync();
    }
  });

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    if (target.id !== "tags-view-detail") return;
    scrollMainContentTop();
  });

  document.addEventListener("app:rehydrate", (event) => {
    if (shouldForceRestoreCycle(event?.detail?.reason)) {
      resetEntriesRestoreState();
    }
    sync(event?.detail?.context || document);
  });
  document.addEventListener("app:view-changed", (event) => {
    if (event?.detail?.view === "tags") return;
    resetEntriesRestoreState();
  });
  document.addEventListener("app:teardown", () => {
    captureEntriesAnchor();
  });
  window.addEventListener("pagehide", () => {
    captureEntriesAnchor();
    resetEntriesRestoreState();
  });

  sync(document);
}
