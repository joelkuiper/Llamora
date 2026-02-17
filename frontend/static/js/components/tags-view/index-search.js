import { readTagsCatalog } from "../../services/tags-catalog.js";
import { prefersReducedMotion } from "../../utils/motion.js";
import { sessionStore } from "../../utils/storage.js";
import { getSelectedTrace, refreshDetailLinksForSort } from "./detail.js";
import { findDetail, findList, findListBody, findSidebar } from "./dom.js";
import { getTagsDay, readTagFromUrl, updateUrlSort } from "./router.js";
import { state } from "./state.js";

export const readStoredSearchQuery = () => sessionStore.get("tags:query") ?? "";

export const persistSearchQuery = (value) => {
  if (value) {
    sessionStore.set("tags:query", value);
  } else {
    sessionStore.delete("tags:query");
  }
};

export const setClearButtonVisibility = () => {
  if (!(state.clearBtn instanceof HTMLButtonElement)) return;
  const hasQuery = Boolean(String(state.query || "").trim());
  state.clearBtn.classList.toggle("is-visible", hasQuery);
  state.clearBtn.setAttribute("aria-hidden", hasQuery ? "false" : "true");
  state.clearBtn.tabIndex = hasQuery ? 0 : -1;
};

const getSelectedTagName = (root = document) => getSelectedTrace(root);

export const findRowByTagName = (tagName) => {
  if (!tagName) return null;
  return state.rows.find((row) => row.dataset.tagName === tagName) || null;
};

const getIndexItemByName = (tagName) => {
  if (!tagName) return null;
  if (!Array.isArray(state.indexItems)) return null;
  return state.indexItems.find((item) => item.name === tagName) || null;
};

const normalizeTagKey = (value) =>
  String(value || "")
    .trim()
    .toLowerCase();

const normalizeAlphaSortToken = (name, kind = "", label = "") => {
  const normalizedKind = String(kind || "")
    .trim()
    .toLowerCase();
  const normalizedLabel = String(label || "").trim();
  if (
    normalizedKind === "emoji" &&
    normalizedLabel.startsWith(":") &&
    normalizedLabel.endsWith(":")
  ) {
    const base = normalizedLabel.slice(1, -1).trim();
    if (base) {
      return base.replaceAll("_", " ").toLowerCase();
    }
  }
  return String(name || "")
    .trim()
    .toLowerCase();
};

export const resetIndexCache = () => {
  state.indexItems = null;
  state.indexPending = false;
  state.listBuilt = false;
  state.indexSignature = "";
};

export const hydrateIndexFromTemplate = (root = document) => {
  const snapshot = readTagsCatalog(root);
  const signature = String(snapshot.version);
  if (state.indexSignature === signature) return;
  state.indexItems = snapshot.items
    .map((item) => ({
      name: String(item?.name || "").trim(),
      hash: String(item?.hash || "").trim(),
      count: Number.parseInt(item?.count || "0", 10) || 0,
      kind:
        String(item?.kind || "")
          .trim()
          .toLowerCase() === "emoji"
          ? "emoji"
          : "text",
      label: String(item?.label || "").trim(),
    }))
    .filter((item) => item.name);
  state.listBuilt = false;
  state.indexSignature = signature;
};

const loadIndexItems = async () => {
  if (Array.isArray(state.indexItems)) return state.indexItems;
  hydrateIndexFromTemplate(document);
  return Array.isArray(state.indexItems) ? state.indexItems : [];
};

const buildTagUrls = (tagName, tagHash) => {
  const day = getTagsDay();
  if (!day) return null;
  const baseTagUrl = `/t/${encodeURIComponent(tagName)}`;
  const params = new URLSearchParams({
    sort_kind: state.sortKind,
    sort_dir: state.sortDir,
  });
  const currentUrl = new URL(window.location.href);
  const dayParam = String(currentUrl.searchParams.get("day") || "").trim();
  if (dayParam) {
    params.set("day", dayParam);
  }
  const tagUrl = params.toString() ? `${baseTagUrl}?${params.toString()}` : baseTagUrl;
  const fragmentParams = new URLSearchParams({
    sort_kind: state.sortKind,
    sort_dir: state.sortDir,
    tag: tagName,
  });
  if (tagHash) {
    fragmentParams.set("tag_hash", tagHash);
  }
  const fragmentUrl = `/fragments/tags/${day}/detail?${fragmentParams.toString()}`;
  return { tagUrl, fragmentUrl };
};

const createTagRow = (item, { transient = false } = {}) => {
  const urls = buildTagUrls(item.name, item.hash);
  if (!urls) return null;
  const row = document.createElement("a");
  row.className = "tags-view__index-row";
  row.id = `tag-index-${item.name}`;
  row.dataset.tagName = item.name;
  row.dataset.tagsName = item.name;
  row.dataset.tagsCount = String(item.count ?? 0);
  row.dataset.tagsKind = item.kind || "text";
  row.dataset.tagsLabel = String(item.label || "").trim();
  if (transient) {
    row.dataset.tagsTransient = "true";
  }
  row.setAttribute("href", urls.tagUrl);
  row.setAttribute("hx-get", urls.fragmentUrl);
  row.setAttribute("hx-target", "#tags-view-detail");
  row.setAttribute("hx-swap", "outerHTML");
  row.setAttribute("hx-sync", "#tags-view-detail:replace");
  row.setAttribute("hx-push-url", urls.tagUrl);
  const nameEl = document.createElement("span");
  nameEl.className = "tags-view__index-name";
  const nameMainEl = document.createElement("span");
  nameMainEl.className = "tags-view__index-name-main";
  nameMainEl.textContent = item.name;
  nameEl.appendChild(nameMainEl);
  const hint = String(item.label || "").trim();
  if (hint) {
    const hintEl = document.createElement("span");
    hintEl.className = "tags-view__index-name-hint";
    hintEl.textContent = hint;
    nameEl.appendChild(hintEl);
  }
  const countEl = document.createElement("span");
  countEl.className = "tags-view__index-count";
  countEl.textContent = String(item.count ?? 0);
  row.appendChild(nameEl);
  row.appendChild(countEl);
  return row;
};

const getIndexOrderMap = () => {
  const map = new Map();
  if (!Array.isArray(state.indexItems)) return map;
  state.indexItems.forEach((item, idx) => {
    map.set(item.name, idx);
  });
  return map;
};

const removeTransientRow = (tagName) => {
  if (!tagName) return;
  const list = findList();
  const index = list?.querySelector?.("[data-tags-view-index]");
  if (!index) return;
  const rows = index.querySelectorAll('[data-tags-transient="true"]');
  rows.forEach((row) => {
    if (row instanceof HTMLElement && row.dataset.tagName === tagName) {
      row.remove();
    }
  });
};

export const ensureActiveRowPresent = (tagName) => {
  if (!tagName) return;
  if (findRowByTagName(tagName)) {
    removeTransientRow(tagName);
    return;
  }
  if (Array.isArray(state.indexItems) && !state.listBuilt) {
    buildIndexListIfNeeded(document);
    if (findRowByTagName(tagName)) {
      removeTransientRow(tagName);
      return;
    }
  }
  if (!state.indexItems && !state.indexPending) {
    state.indexPending = true;
    loadIndexItems().then(() => {
      state.indexPending = false;
      ensureActiveRowPresent(tagName);
    });
    return;
  }
  const item = getIndexItemByName(tagName);
  if (!item) return;
  const list = findList();
  const index = list?.querySelector?.("[data-tags-view-index]");
  if (!index) return;
  removeTransientRow(tagName);
  const transient = createTagRow(item, { transient: true });
  if (!transient) return;
  const orderMap = getIndexOrderMap();
  const targetIndex = orderMap.get(tagName);
  let inserted = false;
  if (targetIndex != null) {
    const existingRows = Array.from(index.querySelectorAll(".tags-view__index-row"));
    for (const row of existingRows) {
      const rowName = row.dataset.tagName || "";
      const rowIndex = orderMap.get(rowName);
      if (rowIndex != null && rowIndex > targetIndex) {
        index.insertBefore(transient, row);
        inserted = true;
        break;
      }
    }
  }
  if (!inserted) {
    index.appendChild(transient);
  }
  if (typeof htmx !== "undefined" && transient instanceof HTMLElement) {
    htmx.process(transient);
  }
  buildSearchIndex(document);
  if (state.query) {
    applySearch(state.query);
  }
};

export const buildIndexListIfNeeded = (root = document) => {
  const list = findList(root);
  const index = list?.querySelector?.("[data-tags-view-index]");
  if (!index) return;
  if (index.children.length === 0) {
    state.listBuilt = false;
  }
  if (state.listBuilt) return;
  if (!Array.isArray(state.indexItems)) return;
  if (index.children.length) {
    state.listBuilt = true;
    return;
  }
  const frag = document.createDocumentFragment();
  state.indexItems.forEach((item) => {
    const row = createTagRow(item);
    if (row) frag.appendChild(row);
  });
  index.appendChild(frag);
  if (typeof htmx !== "undefined") {
    htmx.process(index);
  }
  state.listBuilt = true;
  buildSearchIndex(root);
  applySearch(state.query);
};

export const rebuildIndexList = (root = document) => {
  const list = findList(root);
  const index = list?.querySelector?.("[data-tags-view-index]");
  if (!index) return;
  index.innerHTML = "";
  state.listBuilt = false;
  buildIndexListIfNeeded(root);
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

const isRowInView = (row, container, padding = 8) => {
  const rowTop = row.offsetTop;
  const rowBottom = rowTop + row.offsetHeight;
  const viewTop = container.scrollTop + padding;
  const viewBottom = container.scrollTop + container.clientHeight - padding;
  return rowTop >= viewTop && rowBottom <= viewBottom;
};

const scrollRowIntoView = (row, container, behavior = "auto") => {
  const centerTop = row.offsetTop - (container.clientHeight - row.offsetHeight) / 2;
  const nextTop = Math.max(0, Math.round(centerTop));
  container.scrollTo({ top: nextTop, behavior });
};

export const scrollActiveRowIntoView = (root = document, behavior = "smooth") => {
  const listBody = findListBody(root);
  const activeRow = getActiveRow();
  if (!(listBody instanceof HTMLElement) || !(activeRow instanceof HTMLElement)) return;
  if (isRowInView(activeRow, listBody)) return;
  const scrollBehavior = prefersReducedMotion() ? "auto" : behavior;
  const attemptScroll = () => {
    if (!activeRow.isConnected || !listBody.isConnected) return;
    if (isRowInView(activeRow, listBody)) return;
    scrollRowIntoView(activeRow, listBody, scrollBehavior);
  };
  window.requestAnimationFrame(() => {
    attemptScroll();
    window.requestAnimationFrame(() => {
      attemptScroll();
      window.setTimeout(attemptScroll, 120);
      window.setTimeout(attemptScroll, 240);
    });
  });
};

export const setActiveTag = (tagName, root = document, options = {}) => {
  const behavior = options.behavior === "smooth" ? "smooth" : "auto";
  const shouldScroll = options.scroll !== false;
  const list = findList(root);
  if (!list) return;
  const targetName = String(tagName || "").trim();
  const index = list.querySelector?.("[data-tags-view-index]");
  let removedTransient = false;
  if (index) {
    index.querySelectorAll('[data-tags-transient="true"]').forEach((row) => {
      if (!(row instanceof HTMLElement)) return;
      if (row.dataset.tagName !== targetName) {
        row.remove();
        removedTransient = true;
      }
    });
  }
  if (removedTransient) {
    buildSearchIndex(root);
  }
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
  if (!shouldScroll) return;
  const listBody = findListBody(root);
  if (listBody instanceof HTMLElement) {
    if (!isRowInView(activeRow, listBody)) {
      state.pendingListScroll = true;
      scrollRowIntoView(activeRow, listBody, behavior);
    }
    if (behavior === "auto") {
      window.requestAnimationFrame(() => {
        if (!activeRow.isConnected || !listBody.isConnected) return;
        if (isRowInView(activeRow, listBody)) return;
        state.pendingListScroll = true;
        scrollRowIntoView(activeRow, listBody, "auto");
      });
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          if (!activeRow.isConnected || !listBody.isConnected) return;
          if (isRowInView(activeRow, listBody)) return;
          state.pendingListScroll = true;
          scrollRowIntoView(activeRow, listBody, "auto");
        });
      });
    }
    return;
  }
  activeRow.scrollIntoView({ block: "center", inline: "nearest", behavior });
};

export const getActiveRow = () => {
  if (!state.rows.length) return null;
  return state.rows.find((row) => row.classList.contains("is-active")) || null;
};

const ensureActiveRowVisibleInFilteredSet = (matches, orderedMatches) => {
  const activeRow = getActiveRow();
  if (!(activeRow instanceof HTMLElement)) return orderedMatches;
  if (matches.has(activeRow)) return orderedMatches;
  matches.add(activeRow);
  return [activeRow, ...orderedMatches];
};

const escapeHtml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const normalizeQueryTokens = (query) =>
  String(query || "")
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean);

const highlightMatch = (text, queryTokens) => {
  if (!queryTokens?.length) return escapeHtml(text);
  const lowerText = text.toLowerCase();
  const ranges = [];
  queryTokens.forEach((token) => {
    if (!token) return;
    let start = 0;
    while (start < lowerText.length) {
      const idx = lowerText.indexOf(token, start);
      if (idx < 0) break;
      ranges.push([idx, idx + token.length]);
      start = idx + token.length;
    }
  });
  if (!ranges.length) return escapeHtml(text);
  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const [start, end] of ranges) {
    const last = merged[merged.length - 1];
    if (!last || start > last[1]) {
      merged.push([start, end]);
    } else {
      last[1] = Math.max(last[1], end);
    }
  }
  let out = "";
  let cursor = 0;
  merged.forEach(([start, end]) => {
    out += escapeHtml(text.slice(cursor, start));
    out += `<mark>${escapeHtml(text.slice(start, end))}</mark>`;
    cursor = end;
  });
  out += escapeHtml(text.slice(cursor));
  return out;
};

const captureRowPositions = (rows) => {
  const positions = new Map();
  rows.forEach((row) => {
    if (!(row instanceof HTMLElement)) return;
    if (row.classList.contains("is-filtered-out")) return;
    positions.set(row, row.getBoundingClientRect());
  });
  return positions;
};

const runFlip = (rows, beforePositions) => {
  if (!beforePositions || beforePositions.size === 0) return;
  const moves = [];
  rows.forEach((row) => {
    if (!(row instanceof HTMLElement)) return;
    if (row.classList.contains("is-filtered-out")) return;
    const first = beforePositions.get(row);
    if (!first) return;
    const last = row.getBoundingClientRect();
    const deltaY = first.top - last.top;
    if (Math.abs(deltaY) < 1) return;
    moves.push({ row, deltaY });
  });
  if (!moves.length) return;
  moves.forEach(({ row, deltaY }) => {
    row.style.transform = `translateY(${deltaY}px)`;
    row.style.transition = "transform 0s";
  });
  window.requestAnimationFrame(() => {
    moves.forEach(({ row }) => {
      row.style.transition = "";
      row.style.transform = "";
    });
  });
};

const _sortRows = () => {
  if (!state.list || state.rows.length <= 1) return;

  const sorted = [...state.rows].sort((a, b) => {
    const nameA = String(a.dataset.tagsName || "").toLowerCase();
    const nameB = String(b.dataset.tagsName || "").toLowerCase();
    const alphaA = normalizeAlphaSortToken(
      a.dataset.tagsName,
      a.dataset.tagsKind,
      a.dataset.tagsLabel,
    );
    const alphaB = normalizeAlphaSortToken(
      b.dataset.tagsName,
      b.dataset.tagsKind,
      b.dataset.tagsLabel,
    );
    const countA = Number.parseInt(a.dataset.tagsCount || "0", 10) || 0;
    const countB = Number.parseInt(b.dataset.tagsCount || "0", 10) || 0;

    if (state.sortKind === "count") {
      if (countA !== countB) {
        return state.sortDir === "desc" ? countB - countA : countA - countB;
      }
      const alphaCmp = alphaA.localeCompare(alphaB);
      if (alphaCmp !== 0) return alphaCmp;
      return nameA.localeCompare(nameB);
    }

    const alphaCmp = alphaA.localeCompare(alphaB);
    if (alphaCmp !== 0) {
      return state.sortDir === "desc" ? -alphaCmp : alphaCmp;
    }
    const nameCmp = nameA.localeCompare(nameB);
    return state.sortDir === "desc" ? -nameCmp : nameCmp;
  });

  sorted.forEach((row) => {
    state.list.appendChild(row);
  });
  state.rows = sorted;
};

export const buildSearchIndex = (root = document) => {
  const list = findList(root) || document.querySelector("#tags-view-list");
  const sidebar = findSidebar(root);
  state.list = list?.querySelector("[data-tags-view-index]") || null;
  state.rows = Array.from(state.list?.querySelectorAll(".tags-view__index-row") || []);
  state.input = sidebar?.querySelector("[data-tags-view-search]") || null;
  state.clearBtn = sidebar?.querySelector("[data-tags-view-search-clear]") || null;
  state.empty = list?.querySelector("[data-tags-view-empty]") || null;

  state.rows.forEach((row, index) => {
    const nameEl = row.querySelector(".tags-view__index-name-main");
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

export const applySearch = (rawQuery) => {
  if (!state.rows.length) {
    buildSearchIndex();
  }
  const previousQuery = state.query;
  const query = String(rawQuery || "").trim();
  state.query = query;
  persistSearchQuery(query);
  setClearButtonVisibility();
  const listBody = findListBody();
  if (!state.rows.length) return;
  const beforePositions = captureRowPositions(state.rows);
  if (listBody && query !== previousQuery) {
    listBody.scrollTop = 0;
  }

  if (!query) {
    state.list?.classList.remove("is-filtering");
    state.rows.forEach((row) => {
      row.classList.remove("is-filtered-out");
      row.removeAttribute("aria-hidden");
      const nameEl = row.querySelector(".tags-view__index-name-main");
      if (nameEl instanceof HTMLElement) {
        const original = nameEl.dataset.originalText || row.dataset.tagsName || "";
        nameEl.textContent = original;
      }
    });
    if (state.list) {
      _sortRows();
    }
    if (state.empty) {
      state.empty.hidden = true;
    }
    runFlip(state.rows, beforePositions);
    if (listBody && previousQuery && !query) {
      scrollActiveRowIntoView(document, "auto");
    }
    return;
  }
  state.list?.classList.add("is-filtering");

  if (!state.indexItems && !state.indexPending) {
    state.indexPending = true;
    const loaded = loadIndexItems();
    state.indexPending = false;
    if (loaded?.length) {
      applySearch(state.query);
    }
  }

  const queryTokens = normalizeQueryTokens(query);
  const normalized = query.toLowerCase();
  const indexItems =
    Array.isArray(state.indexItems) && state.indexItems.length ? state.indexItems : null;
  const candidates = indexItems
    ? indexItems.map((item) => {
        const label = String(item.label || "").trim();
        const keyParts = [item.name, label, label.replaceAll(":", " ").replaceAll("_", " ")];
        return {
          ...item,
          key: normalizeTagKey(keyParts.filter(Boolean).join(" ")),
        };
      })
    : state.rows.map((row) => ({
        name: row.dataset.tagsName || "",
        hash: "",
        count: Number.parseInt(row.dataset.tagsCount || "0", 10) || 0,
        key: normalizeTagKey(row.dataset.tagsName || ""),
      }));

  const matchesTokens = (key) => queryTokens.every((token) => key.includes(token));
  const orderedItems = candidates
    .filter((item) =>
      queryTokens.length ? matchesTokens(item.key) : item.key.includes(normalized),
    )
    .sort((a, b) => {
      const aIndex = queryTokens.length
        ? Math.min(...queryTokens.map((token) => a.key.indexOf(token)))
        : a.key.indexOf(normalized);
      const bIndex = queryTokens.length
        ? Math.min(...queryTokens.map((token) => b.key.indexOf(token)))
        : b.key.indexOf(normalized);
      if (aIndex !== bIndex) {
        return aIndex - bIndex;
      }
      return a.key.localeCompare(b.key);
    });

  const selectedTag = getSelectedTagName();
  if (indexItems && orderedItems.length) {
    ensureActiveRowPresent(selectedTag);
  }

  let orderedMatches = orderedItems
    .map((item) => findRowByTagName(item.name))
    .filter((row) => row instanceof HTMLElement);
  const matches = new Set(orderedMatches);
  orderedMatches = ensureActiveRowVisibleInFilteredSet(matches, orderedMatches);
  const applyFilter = () => {
    if (state.list) {
      const remainder = state.rows.filter((row) => !matches.has(row));
      [...orderedMatches, ...remainder].forEach((row) => {
        state.list.appendChild(row);
      });
    }
    let visibleCount = 0;
    state.rows.forEach((row) => {
      const isVisible = matches.has(row);
      row.classList.toggle("is-filtered-out", !isVisible);
      row.setAttribute("aria-hidden", isVisible ? "false" : "true");
      if (isVisible) {
        const mainEl = row.querySelector(".tags-view__index-name-main");
        if (mainEl instanceof HTMLElement) {
          const original = mainEl.dataset.originalText || row.dataset.tagsName || "";
          mainEl.innerHTML = highlightMatch(original, queryTokens);
        }
      }
      if (isVisible) visibleCount += 1;
    });
    if (state.empty) {
      state.empty.hidden = visibleCount > 0 || state.indexPending;
    }
    runFlip(state.rows, beforePositions);
  };
  if (listBody) {
    window.requestAnimationFrame(() => {
      if (!listBody.isConnected) return;
      applyFilter();
    });
  } else {
    applyFilter();
  }
};

let searchTimer = null;
let pendingQuery = "";
export const scheduleSearch = (value, { immediate = false } = {}) => {
  pendingQuery = String(value ?? "");
  if (searchTimer) {
    window.clearTimeout(searchTimer);
    searchTimer = null;
  }
  if (immediate) {
    applySearch(pendingQuery);
    return;
  }
  searchTimer = window.setTimeout(() => {
    searchTimer = null;
    applySearch(pendingQuery);
  }, 140);
};

export const syncSortStateFromDom = (root = document) => {
  const detail = findDetail(root);
  const list = findList(root);
  const detailKind = detail?.dataset?.sortKind;
  const detailDir = detail?.dataset?.sortDir;
  const listKind = list?.dataset?.sortKind;
  const listDir = list?.dataset?.sortDir;
  const rawKind = listKind || detailKind;
  const rawDir = listDir || detailDir;
  const prevKind = state.sortKind;
  const prevDir = state.sortDir;
  state.sortKind = rawKind ? normalizeSortKind(rawKind) : state.sortKind || "count";
  state.sortDir = rawDir ? normalizeSortDir(rawDir) : state.sortDir || "desc";
  if (prevKind !== state.sortKind || prevDir !== state.sortDir) {
    resetIndexCache();
  }
};

export const updateSortButtons = (root = document) => {
  const sidebar = findSidebar(root);
  if (!sidebar) return;
  sidebar.querySelectorAll("[data-tags-sort-kind][data-tags-sort-dir]").forEach((button) => {
    const isActive =
      normalizeSortKind(button.dataset.tagsSortKind) === state.sortKind &&
      normalizeSortDir(button.dataset.tagsSortDir) === state.sortDir;
    button.classList.toggle("is-active", isActive);
  });
};

export const applySort = (kind, dir, { updateUrl = true, refreshSearch = true } = {}) => {
  state.sortKind = normalizeSortKind(kind);
  state.sortDir = normalizeSortDir(dir);
  resetIndexCache();
  const detail = findDetail();
  if (detail) {
    detail.dataset.sortKind = state.sortKind;
    detail.dataset.sortDir = state.sortDir;
  }
  updateSortButtons();
  if (refreshSearch) {
    applySearch(state.query);
  }
  if (updateUrl) {
    updateUrlSort({
      sortKind: state.sortKind,
      sortDir: state.sortDir,
      selectedTag: detail?.dataset?.selectedTag,
    });
  }
};

export const requestSort = (kind, dir) => {
  const nextKind = normalizeSortKind(kind);
  const nextDir = normalizeSortDir(dir);

  const detail = findDetail();
  const list = findList();
  if (!(detail instanceof HTMLElement)) {
    applySort(nextKind, nextDir, { updateUrl: true });
    return;
  }

  const selectedTag =
    String(detail.dataset.selectedTag || "").trim() || getSelectedTrace() || readTagFromUrl();

  const tagPath = selectedTag ? `/t/${encodeURIComponent(selectedTag)}` : "/t";
  const pageParams = new URLSearchParams({
    sort_kind: nextKind,
    sort_dir: nextDir,
  });
  const currentUrl = new URL(window.location.href);
  const dayParam = String(currentUrl.searchParams.get("day") || "").trim();
  if (dayParam) {
    pageParams.set("day", dayParam);
  }
  const pageUrl = pageParams.toString() ? `${tagPath}?${pageParams.toString()}` : tagPath;

  state.sortKind = nextKind;
  state.sortDir = nextDir;
  resetIndexCache();
  if (detail) {
    detail.dataset.sortKind = nextKind;
    detail.dataset.sortDir = nextDir;
  }
  if (list) {
    list.dataset.sortKind = nextKind;
    list.dataset.sortDir = nextDir;
  }
  updateSortButtons();
  refreshDetailLinksForSort(document);

  window.history.pushState(window.history.state, "", pageUrl);
  captureListPositions();
  hydrateIndexFromTemplate(document);
  const finalizeSort = () => {
    if (selectedTag) {
      ensureActiveRowPresent(selectedTag);
      setActiveTag(selectedTag, document, { behavior: "auto", scroll: false });
    }
    animateListReorder(() => {
      if (selectedTag) {
        scrollActiveRowIntoView(document, "smooth");
      }
    });
  };
  if (Array.isArray(state.indexItems)) {
    state.indexItems = state.indexItems.slice().sort((a, b) => {
      const nameA = a.name.toLowerCase();
      const nameB = b.name.toLowerCase();
      const alphaA = normalizeAlphaSortToken(a.name, a.kind, a.label);
      const alphaB = normalizeAlphaSortToken(b.name, b.kind, b.label);
      const countA = Number.parseInt(a.count || "0", 10) || 0;
      const countB = Number.parseInt(b.count || "0", 10) || 0;
      if (nextKind === "count") {
        if (countA !== countB) {
          return nextDir === "desc" ? countB - countA : countA - countB;
        }
        const alphaCmp = alphaA.localeCompare(alphaB);
        if (alphaCmp !== 0) return alphaCmp;
        return nameA.localeCompare(nameB);
      }
      const alphaCmp = alphaA.localeCompare(alphaB);
      if (alphaCmp !== 0) {
        return nextDir === "desc" ? -alphaCmp : alphaCmp;
      }
      const nameCmp = nameA.localeCompare(nameB);
      return nextDir === "desc" ? -nameCmp : nameCmp;
    });
    rebuildIndexList(document);
    finalizeSort();
  } else {
    loadIndexItems().then(() => {
      rebuildIndexList(document);
      finalizeSort();
    });
  }
  updateUrlSort({ sortKind: state.sortKind, sortDir: state.sortDir, selectedTag: selectedTag });
};

export const captureListPositions = () => {
  if (prefersReducedMotion()) {
    state.listPositions = null;
    return;
  }
  const list = findList();
  if (!list) return;
  const positions = new Map();
  list.querySelectorAll(".tags-view__index-row").forEach((row) => {
    if (!(row instanceof HTMLElement)) return;
    positions.set(row.dataset.tagName || row.id, row.getBoundingClientRect().top);
  });
  state.listPositions = positions;
};

export const animateListReorder = (onFinish) => {
  const list = findList();
  if (!list || !state.listPositions) {
    if (typeof onFinish === "function") onFinish();
    return;
  }
  const animations = [];
  list.querySelectorAll(".tags-view__index-row").forEach((row) => {
    if (!(row instanceof HTMLElement)) return;
    const key = row.dataset.tagName || row.id;
    const prevTop = state.listPositions.get(key);
    if (prevTop == null) return;
    const nextTop = row.getBoundingClientRect().top;
    const delta = prevTop - nextTop;
    if (Math.abs(delta) < 2) return;
    animations.push(
      row.animate([{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }], {
        duration: 220,
        easing: "cubic-bezier(0.22, 0.61, 0.36, 1)",
      }),
    );
  });
  state.listPositions = null;
  if (typeof onFinish === "function") {
    if (animations.length) {
      Promise.all(animations.map((a) => a.finished.catch(() => {}))).then(onFinish);
    } else {
      onFinish();
    }
  }
};

export const normalizeSort = {
  kind: normalizeSortKind,
  dir: normalizeSortDir,
};
