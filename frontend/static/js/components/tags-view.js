import * as FuseModule from "fuse.js";
import { armEntryAnimations, armInitialEntryAnimations } from "../entries/entry-animations.js";

const FuseCtor = FuseModule.default ?? FuseModule;

const BOOT_KEY = "__llamoraTagsViewBooted";
const SEARCH_QUERY_STORAGE_KEY = "llamora:tags-view:query";

const state = {
  query: "",
  sortKind: "count",
  sortDir: "desc",
  fuse: null,
  rows: [],
  input: null,
  empty: null,
  list: null,
};

const readStoredSearchQuery = () => {
  try {
    return String(window.sessionStorage.getItem(SEARCH_QUERY_STORAGE_KEY) || "").trim();
  } catch {
    return "";
  }
};

const persistSearchQuery = (value) => {
  try {
    if (value) {
      window.sessionStorage.setItem(SEARCH_QUERY_STORAGE_KEY, value);
    } else {
      window.sessionStorage.removeItem(SEARCH_QUERY_STORAGE_KEY);
    }
  } catch {
    // Ignore storage failures.
  }
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

const updateUrlWithSort = (rawUrl) => {
  const current = new URL(window.location.href);
  const next = new URL(rawUrl, current.origin);
  next.searchParams.set("sort_kind", state.sortKind);
  next.searchParams.set("sort_dir", state.sortDir);
  return `${next.pathname}${next.search}${next.hash}`;
};

const ensureSortParams = (element) => {
  if (!(element instanceof Element)) return;
  const href = element.getAttribute("href");
  const hxGet = element.getAttribute("hx-get");
  const hxPush = element.getAttribute("hx-push-url");
  if (href) element.setAttribute("href", updateUrlWithSort(href));
  if (hxGet) element.setAttribute("hx-get", updateUrlWithSort(hxGet));
  if (hxPush) element.setAttribute("hx-push-url", updateUrlWithSort(hxPush));
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
  armEntryAnimations(detail);
  const entries = detail.querySelector(".tags-view__entries");
  if (!(entries instanceof HTMLElement)) return;
  armInitialEntryAnimations(entries);
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
  updateHeaderHeight();
  if (!state.query) {
    state.query = readStoredSearchQuery();
  }
  syncFromDetail(root);
  buildSearchIndex(root);
  syncSortLinks();
  sortRows();
  applySearch(state.query);
  syncFromDetail(root);
  animateDetailEntries(root);
};

if (!globalThis[BOOT_KEY]) {
  globalThis[BOOT_KEY] = true;
  window.addEventListener("resize", updateHeaderHeight);

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

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
    if (
      target.id === "tags-view-detail" ||
      target.id === "main-content" ||
      target.id === "tags-view-list"
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
    sync(event?.detail?.context || document);
  });

  sync(document);
}
