import { readTagsCatalog } from "../../services/tags-catalog.js";
import {
  normalizeTagsSortDir,
  normalizeTagsSortKind,
  readTagsSortState,
  writeTagsSortState,
} from "../../services/tags-sort-store.js";
import { prefersReducedMotion } from "../../utils/motion.js";
import { sessionStore } from "../../utils/storage.js";
import { HyperList } from "../../vendor/setup-globals.js";
import { getSelectedTrace, refreshDetailLinksForNav } from "./detail.js";
import { findDetail, findList, findListBody, findSidebar } from "./dom.js";
import { getTagsDay, readTagFromUrl, syncTagsHistoryUrl } from "./router.js";
import { requestListScroll, state } from "./state.js";
import { buildTagDetailFragmentUrl, buildTagPageUrl } from "./tags-nav-url.js";

const VIRTUAL_ITEM_HEIGHT_FALLBACK = 42;

const ensureVirtualState = () => {
  state.visibleItems ??= [];
  state.queryTokens ??= [];
  state.activeTag ??= "";
  state.hyperList ??= null;
  state.hyperListRoot ??= null;
  state.hyperListScroller ??= null;
  state.rowHeight ??= VIRTUAL_ITEM_HEIGHT_FALLBACK;
};

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

const escapeSelectorValue = (value) => {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(String(value || ""));
  }
  return String(value || "").replaceAll('"', '\\"');
};

const getSelectedTagName = (root = document) => getSelectedTrace(root);

export const findRowByTagName = (tagName, root = document) => {
  const normalized = String(tagName || "").trim();
  if (!normalized) return null;
  const index = findList(root)?.querySelector?.("[data-tags-view-index]");
  if (!(index instanceof HTMLElement)) return null;
  const escaped = escapeSelectorValue(normalized);
  return index.querySelector?.(`.tags-view__index-row[data-tag-name="${escaped}"]`) || null;
};

const getIndexItemByName = (tagName) => {
  const normalized = String(tagName || "").trim();
  if (!normalized) return null;
  if (!Array.isArray(state.indexItems)) return null;
  return state.indexItems.find((item) => item.name === normalized) || null;
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
  destroyVirtualList();
  state.indexItems = null;
  state.indexPending = false;
  state.listBuilt = false;
  state.indexSignature = "";
  state.rows = [];
  state.visibleItems = [];
};

export const hydrateIndexFromTemplate = (root = document) => {
  ensureVirtualState();
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
  const tagUrl = buildTagPageUrl(tagName, { day });
  const fragmentUrl = buildTagDetailFragmentUrl(day, tagName, {
    tagHash,
  });
  if (!fragmentUrl) return null;
  return { tagUrl, fragmentUrl };
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

const createTagRow = (item, { queryTokens = [], activeTag = "" } = {}) => {
  const rowHash = String(item.hash || "").trim();
  const urls = buildTagUrls(item.name, rowHash);
  if (!urls) return null;
  const row = document.createElement("a");
  row.className = "tags-view__index-row";
  row.id = `tag-index-${item.name}`;
  row.dataset.tagName = item.name;
  row.dataset.tagsName = item.name;
  row.dataset.tagsCount = String(item.count ?? 0);
  row.dataset.tagHash = rowHash;
  row.dataset.tagsKind = item.kind || "text";
  row.dataset.tagsLabel = String(item.label || "").trim();
  row.dataset.hxReady = "0";
  row.setAttribute("href", urls.tagUrl);
  row.setAttribute("hx-get", urls.fragmentUrl);
  row.setAttribute("hx-target", "#tags-view-detail");
  row.setAttribute("hx-swap", "outerHTML");
  row.setAttribute("hx-sync", "#tags-view-detail:replace");
  row.setAttribute("hx-push-url", urls.tagUrl);
  if (item.name === activeTag) {
    row.classList.add("is-active");
    row.setAttribute("aria-current", "true");
  }
  const nameEl = document.createElement("span");
  nameEl.className = "tags-view__index-name";
  const nameMainEl = document.createElement("span");
  nameMainEl.className = "tags-view__index-name-main";
  if (queryTokens.length) {
    nameMainEl.innerHTML = highlightMatch(item.name, queryTokens);
  } else {
    nameMainEl.textContent = item.name;
  }
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

const compareItems = (a, b, kind, dir) => {
  const nameA = a.name.toLowerCase();
  const nameB = b.name.toLowerCase();
  const alphaA = normalizeAlphaSortToken(a.name, a.kind, a.label);
  const alphaB = normalizeAlphaSortToken(b.name, b.kind, b.label);
  const countA = Number.parseInt(a.count || "0", 10) || 0;
  const countB = Number.parseInt(b.count || "0", 10) || 0;

  if (kind === "count") {
    if (countA !== countB) {
      return dir === "desc" ? countB - countA : countA - countB;
    }
    const alphaCmp = alphaA.localeCompare(alphaB);
    if (alphaCmp !== 0) return alphaCmp;
    return nameA.localeCompare(nameB);
  }

  const alphaCmp = alphaA.localeCompare(alphaB);
  if (alphaCmp !== 0) {
    return dir === "desc" ? -alphaCmp : alphaCmp;
  }
  const nameCmp = nameA.localeCompare(nameB);
  return dir === "desc" ? -nameCmp : nameCmp;
};

const resolveItemHeight = (index) => {
  if (!(index instanceof HTMLElement)) return VIRTUAL_ITEM_HEIGHT_FALLBACK;
  const probeItem = state.visibleItems[0] || state.indexItems?.[0];
  if (!probeItem) return VIRTUAL_ITEM_HEIGHT_FALLBACK;
  const probe = createTagRow(probeItem, {
    queryTokens: [],
    activeTag: state.activeTag,
  });
  if (!(probe instanceof HTMLElement)) return VIRTUAL_ITEM_HEIGHT_FALLBACK;
  probe.style.visibility = "hidden";
  probe.style.pointerEvents = "none";
  probe.style.position = "absolute";
  probe.style.insetInline = "0";
  index.appendChild(probe);
  const measured = Math.ceil(probe.getBoundingClientRect().height);
  probe.remove();
  return Number.isFinite(measured) && measured > 0 ? measured : VIRTUAL_ITEM_HEIGHT_FALLBACK;
};

const processRenderedRows = (index) => {
  if (!(index instanceof HTMLElement)) return;
  const rows = Array.from(index.querySelectorAll(".tags-view__index-row"));
  state.rows = rows;
  if (typeof htmx === "undefined") return;
  rows.forEach((row) => {
    if (!(row instanceof HTMLElement)) return;
    if (row.dataset.hxReady !== "0") return;
    htmx.process(row);
    row.dataset.hxReady = "1";
  });
};

const destroyVirtualList = () => {
  if (state.hyperList && typeof state.hyperList.destroy === "function") {
    state.hyperList.destroy();
  }
  state.hyperList = null;
  state.hyperListRoot = null;
  state.hyperListScroller = null;
};

const renderVirtualList = (root = document) => {
  ensureVirtualState();
  const list = findList(root);
  const index = list?.querySelector?.("[data-tags-view-index]");
  const listBody = findListBody(root);
  if (!(index instanceof HTMLElement) || !(listBody instanceof HTMLElement)) {
    destroyVirtualList();
    return;
  }

  state.list = index;
  index.classList.add("is-virtual");
  const total = Array.isArray(state.visibleItems) ? state.visibleItems.length : 0;
  const showEmpty = total === 0;
  if (state.empty) {
    state.empty.hidden = !showEmpty;
  }
  if (showEmpty) {
    destroyVirtualList();
    index.innerHTML = "";
    state.rows = [];
    return;
  }

  if (!Number.isFinite(state.rowHeight) || state.rowHeight <= 0) {
    state.rowHeight = resolveItemHeight(index);
  }

  const config = {
    itemHeight: state.rowHeight,
    total,
    scrollerTagName: "div",
    rowClassName: "tags-view__index-row",
    scrollContainer: listBody,
    overrideScrollPosition: () => listBody.scrollTop || 0,
    generate: (rowIndex) => {
      const item = state.visibleItems[rowIndex];
      if (!item) {
        const el = document.createElement("div");
        el.className = "tags-view__index-row";
        return el;
      }
      return createTagRow(item, {
        queryTokens: state.queryTokens,
        activeTag: state.activeTag,
      });
    },
    afterRender: () => {
      processRenderedRows(index);
    },
  };

  const sameRoot = state.hyperListRoot === index;
  const sameScroller = state.hyperListScroller === listBody;
  if (!sameRoot || !sameScroller) {
    destroyVirtualList();
    state.hyperList = HyperList.create(index, config);
    state.hyperListRoot = index;
    state.hyperListScroller = listBody;
  } else {
    state.hyperList.refresh(index, config);
  }
};

const computeFilteredItems = (items, query, queryTokens) => {
  if (!query) {
    return items.slice().sort((a, b) => compareItems(a, b, state.sortKind, state.sortDir));
  }
  const normalized = query.toLowerCase();
  const candidates = items.map((item) => {
    const label = String(item.label || "").trim();
    const keyParts = [item.name, label, label.replaceAll(":", " ").replaceAll("_", " ")];
    return {
      item,
      key: normalizeTagKey(keyParts.filter(Boolean).join(" ")),
    };
  });
  const matchesTokens = (key) => queryTokens.every((token) => key.includes(token));
  return candidates
    .filter((candidate) =>
      queryTokens.length ? matchesTokens(candidate.key) : candidate.key.includes(normalized),
    )
    .sort((a, b) => {
      const aIndex = queryTokens.length
        ? Math.min(...queryTokens.map((token) => a.key.indexOf(token)))
        : a.key.indexOf(normalized);
      const bIndex = queryTokens.length
        ? Math.min(...queryTokens.map((token) => b.key.indexOf(token)))
        : b.key.indexOf(normalized);
      if (aIndex !== bIndex) return aIndex - bIndex;
      return a.key.localeCompare(b.key);
    })
    .map((candidate) => candidate.item);
};

const ensureActiveVisibleInResults = (items, activeTag) => {
  const normalized = String(activeTag || "").trim();
  if (!normalized) return items;
  if (items.some((item) => item.name === normalized)) return items;
  const activeItem = getIndexItemByName(normalized);
  if (!activeItem) return items;
  return [activeItem, ...items];
};

const isRowInView = (row, container, padding = 8) => {
  const rowTop = row.offsetTop;
  const rowBottom = rowTop + row.offsetHeight;
  const viewTop = container.scrollTop + padding;
  const viewBottom = container.scrollTop + container.clientHeight - padding;
  return rowTop >= viewTop && rowBottom <= viewBottom;
};

const scrollIndexIntoView = (index, container, behavior = "auto") => {
  const rowHeight = Number.isFinite(state.rowHeight)
    ? state.rowHeight
    : VIRTUAL_ITEM_HEIGHT_FALLBACK;
  const centerTop = index * rowHeight - (container.clientHeight - rowHeight) / 2;
  const nextTop = Math.max(0, Math.round(centerTop));
  container.scrollTo({ top: nextTop, behavior });
};

export const scrollActiveRowIntoView = (root = document, behavior = "smooth") => {
  ensureVirtualState();
  const listBody = findListBody(root);
  if (!(listBody instanceof HTMLElement)) return;
  const activeTag = String(state.activeTag || "").trim();
  if (!activeTag) return;
  const rendered = findRowByTagName(activeTag, root);
  if (rendered instanceof HTMLElement && isRowInView(rendered, listBody)) return;
  const itemIndex = state.visibleItems.findIndex((item) => item.name === activeTag);
  if (itemIndex < 0) return;
  const scrollBehavior = prefersReducedMotion() ? "auto" : behavior;
  requestListScroll();
  scrollIndexIntoView(itemIndex, listBody, scrollBehavior);
};

export const setActiveTag = (tagName, root = document, options = {}) => {
  ensureVirtualState();
  const behavior = options.behavior === "smooth" ? "smooth" : "auto";
  const shouldScroll = options.scroll !== false;
  const normalized = String(tagName || "").trim();
  if (!normalized) return;
  state.activeTag = normalized;
  if (state.listBuilt) {
    renderVirtualList(root);
  }
  if (!shouldScroll) return;
  scrollActiveRowIntoView(root, behavior);
};

export const getActiveRow = (root = document) => {
  const activeTag = String(state.activeTag || "").trim();
  if (!activeTag) return null;
  return findRowByTagName(activeTag, root);
};

export const ensureActiveRowPresent = (tagName, options = {}) => {
  ensureVirtualState();
  const normalized = String(tagName || "").trim();
  if (!normalized) return;

  if (!Array.isArray(state.indexItems)) {
    if (state.indexPending) return;
    state.indexPending = true;
    loadIndexItems().finally(() => {
      state.indexPending = false;
      ensureActiveRowPresent(normalized, options);
    });
    return;
  }

  if (getIndexItemByName(normalized)) return;
  state.indexItems.push({
    name: normalized,
    hash: String(options.tagHash || "").trim(),
    count: 0,
    kind: "text",
    label: "",
  });
  state.listBuilt = false;
  applySearch(state.query);
};

export const buildIndexListIfNeeded = (root = document) => {
  ensureVirtualState();
  const list = findList(root);
  state.list = list?.querySelector?.("[data-tags-view-index]") || null;
  if (!(state.list instanceof HTMLElement)) return;
  if (!Array.isArray(state.indexItems)) return;
  if (state.listBuilt && state.visibleItems.length) return;

  const selectedTag = getSelectedTagName(root);
  if (selectedTag) {
    state.activeTag = selectedTag;
  }
  state.queryTokens = normalizeQueryTokens(state.query);
  state.visibleItems = ensureActiveVisibleInResults(
    computeFilteredItems(state.indexItems, state.query, state.queryTokens),
    state.activeTag,
  );
  renderVirtualList(root);
  state.listBuilt = true;
};

export const rebuildIndexList = (root = document) => {
  state.listBuilt = false;
  buildIndexListIfNeeded(root);
};

export const buildSearchIndex = (root = document) => {
  ensureVirtualState();
  const list = findList(root) || document.querySelector("#tags-view-list");
  const sidebar = findSidebar(root);
  state.list = list?.querySelector?.("[data-tags-view-index]") || null;
  state.input = sidebar?.querySelector("[data-tags-view-search]") || null;
  state.clearBtn = sidebar?.querySelector("[data-tags-view-search-clear]") || null;
  state.empty = list?.querySelector("[data-tags-view-empty]") || null;

  if (state.input) {
    state.input.value = state.query;
  }
  setClearButtonVisibility();
};

export const applySearch = (rawQuery) => {
  ensureVirtualState();
  const previousQuery = state.query;
  const query = String(rawQuery || "").trim();
  state.query = query;
  persistSearchQuery(query);
  setClearButtonVisibility();

  if (!Array.isArray(state.indexItems)) {
    if (state.indexPending) return;
    state.indexPending = true;
    loadIndexItems().finally(() => {
      state.indexPending = false;
      applySearch(state.query);
    });
    return;
  }

  const listBody = findListBody();
  if (listBody && query !== previousQuery) {
    listBody.scrollTop = 0;
  }

  const selectedTag = getSelectedTagName();
  if (selectedTag) {
    state.activeTag = selectedTag;
  }
  state.queryTokens = normalizeQueryTokens(query);
  state.visibleItems = ensureActiveVisibleInResults(
    computeFilteredItems(state.indexItems, query, state.queryTokens),
    state.activeTag,
  );
  state.listBuilt = true;
  renderVirtualList(document);

  if (listBody && previousQuery && !query) {
    scrollActiveRowIntoView(document, "auto");
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
  const stored = readTagsSortState();
  const rawKind = stored?.sortKind || listKind || detailKind;
  const rawDir = stored?.sortDir || listDir || detailDir;
  const prevKind = state.sortKind;
  const prevDir = state.sortDir;
  state.sortKind = rawKind ? normalizeTagsSortKind(rawKind) : state.sortKind || "count";
  state.sortDir = rawDir ? normalizeTagsSortDir(rawDir) : state.sortDir || "desc";
  if (detail) {
    detail.dataset.sortKind = state.sortKind;
    detail.dataset.sortDir = state.sortDir;
  }
  if (list) {
    list.dataset.sortKind = state.sortKind;
    list.dataset.sortDir = state.sortDir;
  }
  writeTagsSortState({
    sortKind: state.sortKind,
    sortDir: state.sortDir,
  });
  if (prevKind !== state.sortKind || prevDir !== state.sortDir) {
    state.listBuilt = false;
  }
};

export const updateSortButtons = (root = document) => {
  const sidebar = findSidebar(root);
  if (!sidebar) return;
  sidebar.querySelectorAll("[data-tags-sort-kind][data-tags-sort-dir]").forEach((button) => {
    const isActive =
      normalizeTagsSortKind(button.dataset.tagsSortKind) === state.sortKind &&
      normalizeTagsSortDir(button.dataset.tagsSortDir) === state.sortDir;
    button.classList.toggle("is-active", isActive);
  });
};

export const applySort = (kind, dir, { updateUrl = true, refreshSearch = true } = {}) => {
  state.sortKind = normalizeTagsSortKind(kind);
  state.sortDir = normalizeTagsSortDir(dir);
  writeTagsSortState({
    sortKind: state.sortKind,
    sortDir: state.sortDir,
  });
  const detail = findDetail();
  if (detail) {
    detail.dataset.sortKind = state.sortKind;
    detail.dataset.sortDir = state.sortDir;
  }
  updateSortButtons();
  state.listBuilt = false;
  if (refreshSearch) {
    applySearch(state.query);
  }
  if (updateUrl) {
    syncTagsHistoryUrl({ selectedTag: detail?.dataset?.selectedTag });
  }
};

export const requestSort = (kind, dir) => {
  const nextKind = normalizeTagsSortKind(kind);
  const nextDir = normalizeTagsSortDir(dir);

  const detail = findDetail();
  const list = findList();
  if (!(detail instanceof HTMLElement)) {
    applySort(nextKind, nextDir, { updateUrl: true });
    return;
  }

  const selectedTag =
    String(detail.dataset.selectedTag || "").trim() || getSelectedTrace() || readTagFromUrl();

  state.sortKind = nextKind;
  state.sortDir = nextDir;
  writeTagsSortState({
    sortKind: state.sortKind,
    sortDir: state.sortDir,
  });
  if (detail) {
    detail.dataset.sortKind = nextKind;
    detail.dataset.sortDir = nextDir;
  }
  if (list) {
    list.dataset.sortKind = nextKind;
    list.dataset.sortDir = nextDir;
  }
  updateSortButtons();
  refreshDetailLinksForNav(document);
  hydrateIndexFromTemplate(document);
  applySearch(state.query);
  if (selectedTag) {
    ensureActiveRowPresent(selectedTag);
    setActiveTag(selectedTag, document, { behavior: "auto", scroll: false });
    scrollActiveRowIntoView(document, "smooth");
  }
  syncTagsHistoryUrl({ selectedTag });
};

export const captureListPositions = () => {
  state.listPositions = null;
};

export const animateListReorder = (onFinish) => {
  if (typeof onFinish === "function") onFinish();
};

export const normalizeSort = {
  kind: normalizeTagsSortKind,
  dir: normalizeTagsSortDir,
};
