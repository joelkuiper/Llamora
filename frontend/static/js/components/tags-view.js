import { armEntryAnimations, armInitialEntryAnimations } from "../entries/entry-animations.js";
import { formatTimeElements } from "../services/time.js";
import { cacheLoader } from "../services/cache-loader.js";
import { clearScrollTarget, flashHighlight } from "../ui.js";
import { prefersReducedMotion } from "../utils/motion.js";
import { sessionStore } from "../utils/storage.js";
import { transitionHide, transitionShow } from "../utils/transition.js";
import { Fuse as FuseCtor } from "../vendor/setup-globals.js";
import { syncSummarySkeletons } from "../services/summary-skeleton.js";

const BOOT_KEY = "__llamoraTagsViewBooted";
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
  restoreAppliedForLocation: "",
  saveSuppressed: false,
  pendingDetailScrollTop: false,
  pendingTagHighlight: "",
  listPositions: null,
};

const heatmapState = {
  initialized: false,
  tooltip: null,
  dateEl: null,
  summaryEl: null,
  timer: null,
  hideTimer: null,
  activeCell: null,
  activeDate: "",
  requests: new Map(),
  cache: new Map(),
};

const makeDaySummaryKey = (date) => `day:${String(date || "").trim()}`;

const ensureHeatmapTooltip = () => {
  if (heatmapState.tooltip?.isConnected) {
    return heatmapState.tooltip;
  }
  const tooltip = document.createElement("div");
  tooltip.className = "calendar-day-tooltip heatmap-day-tooltip";
  tooltip.hidden = true;
  tooltip.innerHTML =
    '<div class="heatmap-day-tooltip__date"></div><div class="heatmap-day-tooltip__summary"></div>';
  const dateEl = tooltip.querySelector(".heatmap-day-tooltip__date");
  const summaryEl = tooltip.querySelector(".heatmap-day-tooltip__summary");
  heatmapState.tooltip = tooltip;
  heatmapState.dateEl = dateEl;
  heatmapState.summaryEl = summaryEl;
  document.body.appendChild(tooltip);
  return tooltip;
};

const hideHeatmapTooltip = ({ immediate = false } = {}) => {
  if (heatmapState.timer) {
    clearTimeout(heatmapState.timer);
    heatmapState.timer = null;
  }
  if (heatmapState.hideTimer) {
    heatmapState.hideTimer();
    heatmapState.hideTimer = null;
  }
  heatmapState.activeCell = null;
  heatmapState.activeDate = "";
  const tooltip = heatmapState.tooltip;
  if (!tooltip) return;
  if (immediate) {
    tooltip.classList.remove("is-visible");
    tooltip.hidden = true;
    return;
  }
  heatmapState.hideTimer = transitionHide(tooltip, "is-visible", 160);
};

const positionHeatmapTooltip = (tooltip, cell) => {
  if (!(tooltip instanceof HTMLElement) || !(cell instanceof HTMLElement)) return;
  tooltip.hidden = false;
  tooltip.style.visibility = "hidden";

  const cellRect = cell.getBoundingClientRect();
  const tipRect = tooltip.getBoundingClientRect();
  const margin = 10;
  const offset = 10;

  let left = cellRect.left + cellRect.width / 2 - tipRect.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - tipRect.width - margin));

  let top = cellRect.top - tipRect.height - offset;
  if (top < margin) {
    top = cellRect.bottom + offset;
  }
  top = Math.max(margin, Math.min(top, window.innerHeight - tipRect.height - margin));

  tooltip.style.left = `${Math.round(left)}px`;
  tooltip.style.top = `${Math.round(top)}px`;
  tooltip.style.visibility = "visible";
  transitionShow(tooltip, "is-visible");
};

const fetchHeatmapSummary = async (date) => {
  if (!date) return "";
  if (heatmapState.cache.has(date)) {
    return heatmapState.cache.get(date);
  }
  const cached = await cacheLoader.read({
    namespace: "summary",
    key: makeDaySummaryKey(date),
    kind: "text",
  });
  if (cached) {
    heatmapState.cache.set(date, cached);
    return cached;
  }
  if (heatmapState.requests.has(date)) {
    return heatmapState.requests.get(date);
  }
  const request = fetch(`/d/${date}/summary`, {
    headers: { Accept: "application/json" },
  })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      const summary = typeof data?.summary === "string" ? data.summary.trim() : "";
      if (summary) {
        heatmapState.cache.set(date, summary);
        void cacheLoader.write({
          namespace: "summary",
          key: makeDaySummaryKey(date),
          kind: "text",
          value: summary,
        });
      }
      heatmapState.requests.delete(date);
      return summary;
    })
    .catch(() => {
      heatmapState.requests.delete(date);
      return "";
    });
  heatmapState.requests.set(date, request);
  return request;
};

const scheduleHeatmapTooltip = (cell) => {
  const date = String(cell?.dataset?.heatmapDate || "").trim();
  if (!date) return;
  const count = Number(cell?.dataset?.heatmapCount || 0);
  if (!Number.isFinite(count) || count <= 0) {
    return;
  }
  if (heatmapState.timer) {
    clearTimeout(heatmapState.timer);
  }
  heatmapState.activeCell = cell;
  heatmapState.activeDate = date;
  heatmapState.timer = window.setTimeout(async () => {
    if (heatmapState.activeDate !== date) return;
    const tooltip = ensureHeatmapTooltip();
    const label = String(cell.dataset.heatmapLabel || "").trim();
    if (heatmapState.dateEl) {
      heatmapState.dateEl.textContent = label;
    }
    if (heatmapState.summaryEl) {
      heatmapState.summaryEl.innerHTML =
        '<div class="tag-detail-skeleton heatmap-summary-skeleton" aria-hidden="true">' +
        '<span class="tag-detail-skeleton__line"></span>' +
        '<span class="tag-detail-skeleton__line"></span>' +
        '<span class="tag-detail-skeleton__line"></span>' +
        '<span class="tag-detail-skeleton__line"></span>' +
        "</div>";
    }
    positionHeatmapTooltip(tooltip, cell);

    const summary = await fetchHeatmapSummary(date);
    if (heatmapState.activeDate !== date) return;
    if (heatmapState.summaryEl) {
      const text = summary || "Summary unavailable right now.";
      heatmapState.summaryEl.innerHTML = `<p class="summary-fade">${text}</p>`;
    }
    positionHeatmapTooltip(tooltip, cell);
  }, 420);
};

const initHeatmapTooltip = () => {
  if (heatmapState.initialized) return;
  heatmapState.initialized = true;

  document.addEventListener(
    "pointerover",
    (event) => {
      const cell = event.target?.closest?.(".activity-heatmap__cell[data-heatmap-date]");
      if (!(cell instanceof HTMLElement)) return;
      if (event.relatedTarget instanceof Node && cell.contains(event.relatedTarget)) return;
      scheduleHeatmapTooltip(cell);
    },
    true,
  );

  document.addEventListener(
    "pointerout",
    (event) => {
      const cell = event.target?.closest?.(".activity-heatmap__cell[data-heatmap-date]");
      if (!cell) return;
      if (event.relatedTarget instanceof Node && cell.contains(event.relatedTarget)) return;
      hideHeatmapTooltip();
    },
    true,
  );

  document.addEventListener(
    "focusin",
    (event) => {
      const cell = event.target?.closest?.(".activity-heatmap__cell[data-heatmap-date]");
      if (!(cell instanceof HTMLElement)) return;
      scheduleHeatmapTooltip(cell);
    },
    true,
  );

  document.addEventListener(
    "focusout",
    (event) => {
      const cell = event.target?.closest?.(".activity-heatmap__cell[data-heatmap-date]");
      if (!cell) return;
      hideHeatmapTooltip();
    },
    true,
  );

  document.addEventListener("scroll", hideHeatmapTooltip, true);
  window.addEventListener("resize", hideHeatmapTooltip, { passive: true });
  window.addEventListener("blur", hideHeatmapTooltip, { passive: true });
  document.addEventListener("htmx:beforeRequest", hideHeatmapTooltip);
  document.addEventListener("app:teardown", () => hideHeatmapTooltip({ immediate: true }));
  document.addEventListener("app:rehydrate", () => hideHeatmapTooltip({ immediate: true }));
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
const readMainScrollMap = () => sessionStore.get("tags:scroll") ?? {};

const writeEntriesAnchorMap = (map) => {
  sessionStore.set("tags:anchor", map);
};
const writeMainScrollMap = (map) => {
  sessionStore.set("tags:scroll", map);
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

const getTagsLocationKey = (tagOverride) => {
  const url = new URL(window.location.href);
  if (!tagOverride && url.searchParams.get("view") !== "tags") return "";
  const tag = tagOverride || String(url.searchParams.get("tag") || "").trim();
  return `${url.pathname}?view=tags&tag=${tag}`;
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

const getMainScrollTop = () => {
  const scrollElement = getMainScrollElement();
  if (!(scrollElement instanceof HTMLElement)) return null;
  return Math.max(0, Math.round(scrollElement.scrollTop || 0));
};

const storeMainScrollTop = () => {
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

const resetEntriesRestoreState = () => {
  state.restoreAppliedForLocation = "";
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

const maybeRestoreEntriesAnchor = () => {
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
      // Re-apply after content-visibility layout settles
      requestAnimationFrame(() => {
        applyEntriesAnchor(entry, anchor.offset);
      });
    }
  }
  state.restoreAppliedForLocation = currentLocation;
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

const scrollActiveRowIntoView = (root = document, behavior = "smooth") => {
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

const setActiveTag = (tagName, root = document, options = {}) => {
  const behavior = options.behavior === "smooth" ? "smooth" : "auto";
  const shouldScroll = options.scroll !== false;
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
  if (!shouldScroll) return;
  const listBody = findListBody(root);
  if (listBody instanceof HTMLElement) {
    if (!isRowInView(activeRow, listBody)) {
      scrollRowIntoView(activeRow, listBody, behavior);
    }
    if (behavior === "auto") {
      window.requestAnimationFrame(() => {
        if (!activeRow.isConnected || !listBody.isConnected) return;
        if (isRowInView(activeRow, listBody)) return;
        // Retry after layout settles (htmx swap + row animations can shift geometry).
        scrollRowIntoView(activeRow, listBody, "auto");
      });
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          if (!activeRow.isConnected || !listBody.isConnected) return;
          if (isRowInView(activeRow, listBody)) return;
          scrollRowIntoView(activeRow, listBody, "auto");
        });
      });
    }
    return;
  }
  activeRow.scrollIntoView({ block: "center", inline: "nearest", behavior });
};

const getActiveRow = () => {
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

const readTagFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return String(params.get("tag") || "").trim();
};

const readSortFromDom = (root = document) => {
  const detail = findDetail(root);
  const list = findList(root);
  const detailKind = detail?.dataset?.sortKind;
  const detailDir = detail?.dataset?.sortDir;
  const listKind = list?.dataset?.sortKind;
  const listDir = list?.dataset?.sortDir;
  const rawKind = listKind || detailKind;
  const rawDir = listDir || detailDir;
  return {
    kind: rawKind ? normalizeSortKind(rawKind) : state.sortKind || "count",
    dir: rawDir ? normalizeSortDir(rawDir) : state.sortDir || "desc",
  };
};

const updateUrlSortParams = (rawUrl, sortKind, sortDir) => {
  if (!rawUrl) return rawUrl;
  try {
    const current = new URL(window.location.href);
    const next = new URL(rawUrl, current.origin);
    next.searchParams.set("sort_kind", sortKind);
    next.searchParams.set("sort_dir", sortDir);
    return `${next.pathname}${next.search}${next.hash}`;
  } catch {
    return rawUrl;
  }
};

const refreshDetailLinksForSort = (root = document) => {
  const detail = findDetail(root);
  if (!detail) return;
  const kind = state.sortKind;
  const dir = state.sortDir;
  detail.querySelectorAll(".tags-view__related-link, .tags-view__entry-tag").forEach((link) => {
    if (!(link instanceof HTMLAnchorElement)) return;
    const href = link.getAttribute("href");
    const hxGet = link.getAttribute("hx-get");
    const hxPush = link.getAttribute("hx-push-url");
    if (href) link.setAttribute("href", updateUrlSortParams(href, kind, dir));
    if (hxGet) link.setAttribute("hx-get", updateUrlSortParams(hxGet, kind, dir));
    if (hxPush) link.setAttribute("hx-push-url", updateUrlSortParams(hxPush, kind, dir));
  });
  detail.querySelectorAll("entry-tags").forEach((el) => {
    if (!(el instanceof HTMLElement)) return;
    const pageTemplate = el.dataset.tagNavigatePageTemplate;
    const fragmentTemplate = el.dataset.tagNavigateFragmentTemplate;
    const addUrl = el.dataset.addTagUrl;
    const suggestUrl = el.dataset.suggestionsUrl;
    if (pageTemplate) {
      el.dataset.tagNavigatePageTemplate = updateUrlSortParams(pageTemplate, kind, dir);
    }
    if (fragmentTemplate) {
      el.dataset.tagNavigateFragmentTemplate = updateUrlSortParams(fragmentTemplate, kind, dir);
    }
    if (addUrl) {
      el.dataset.addTagUrl = updateUrlSortParams(addUrl, kind, dir);
    }
    if (suggestUrl) {
      el.dataset.suggestionsUrl = updateUrlSortParams(suggestUrl, kind, dir);
    }
  });
};

const syncFromDetail = (root = document) => {
  const detail = findDetail(root);
  const sortFromDom = readSortFromDom(root);
  const urlTag = readTagFromUrl();
  const target = String(new URLSearchParams(window.location.search).get("target") || "").trim();
  const keepTargetScrollForHighlight = target.startsWith("tag-index-");

  state.sortKind = sortFromDom.kind;
  state.sortDir = sortFromDom.dir;

  if (detail) {
    const detailTag = String(detail.dataset.selectedTag || "").trim();
    const selectedTag = detailTag || urlTag;
    if (selectedTag) {
      detail.dataset.selectedTag = selectedTag;
    }
    if (selectedTag) {
      setActiveTag(selectedTag, root, {
        behavior: "auto",
        scroll: !keepTargetScrollForHighlight,
      });
    }
  }
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

const syncSortStateFromDom = (root = document) => {
  const sortFromDom = readSortFromDom(root);
  state.sortKind = sortFromDom.kind;
  state.sortDir = sortFromDom.dir;
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

const getSummaryElement = (root = document) =>
  root.querySelector?.(".tags-view__summary[data-tag-hash]") ||
  document.querySelector(".tags-view__summary[data-tag-hash]");

const hydrateTagsViewSummary = async (root = document) => {
  const summaryEl = getSummaryElement(root);
  if (!summaryEl) return;
  await cacheLoader.hydrate(summaryEl);
};

const cacheTagsViewSummary = (summaryEl) => {
  if (!(summaryEl instanceof HTMLElement)) return;
  void cacheLoader.capture(summaryEl);
};

const highlightRequestedTag = (root = document) => {
  const params = new URLSearchParams(window.location.search);
  const target = String(params.get("target") || "").trim();
  if (!target || !target.startsWith("tag-index-")) return;
  const row = document.getElementById(target);
  if (row instanceof HTMLElement) {
    const tagName = row.dataset.tagName || "";
    if (tagName) {
      setActiveTag(tagName, root, { behavior: "smooth" });
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

const _sortRows = () => {
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

const applySearch = (rawQuery) => {
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
      const nameEl = row.querySelector(".tags-view__index-name");
      if (nameEl instanceof HTMLElement) {
        const original = nameEl.dataset.originalText || row.dataset.tagsName || "";
        nameEl.textContent = original;
      }
    });
    if (state.list) {
      const byIndex = [...state.rows].sort((a, b) => {
        const aIndex = Number.parseInt(a.dataset.tagsIndex || "0", 10);
        const bIndex = Number.parseInt(b.dataset.tagsIndex || "0", 10);
        return aIndex - bIndex;
      });
      byIndex.forEach((row) => {
        state.list.appendChild(row);
      });
      state.rows = byIndex;
    }
    if (state.empty) {
      state.empty.hidden = true;
    }
    runFlip(state.rows, beforePositions);
    return;
  }
  state.list?.classList.add("is-filtering");

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

let searchTimer = null;
let pendingQuery = "";
const scheduleSearch = (value, { immediate = false } = {}) => {
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

const applySort = (kind, dir, { updateUrl = true, refreshSearch = true } = {}) => {
  state.sortKind = normalizeSortKind(kind);
  state.sortDir = normalizeSortDir(dir);
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
    updateUrlSort();
  }
};

const getTagsDay = () =>
  String(
    document.querySelector("#tags-view")?.dataset?.day ||
      window.location.pathname.match(/\/d\/(\d{4}-\d{2}-\d{2})$/)?.[1] ||
      "",
  ).trim();

const getEntriesLimitFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  const raw = String(params.get("entries_limit") || "").trim();
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 12;
};

const requestSort = (kind, dir) => {
  const nextKind = normalizeSortKind(kind);
  const nextDir = normalizeSortDir(dir);

  const detail = findDetail();
  const list = findList();
  const day = getTagsDay();
  if (!(detail instanceof HTMLElement) || !day || typeof htmx === "undefined") {
    applySort(nextKind, nextDir, { updateUrl: true });
    return;
  }

  const entriesLimit = getEntriesLimitFromUrl();
  const selectedTag =
    String(detail.dataset.selectedTag || "").trim() || getSelectedTrace() || readTagFromUrl();

  const fragmentParams = new URLSearchParams({
    sort_kind: nextKind,
    sort_dir: nextDir,
    entries_limit: String(entriesLimit),
  });
  if (selectedTag) {
    fragmentParams.set("tag", selectedTag);
  }

  const pageParams = new URLSearchParams({
    view: "tags",
    sort_kind: nextKind,
    sort_dir: nextDir,
    entries_limit: String(entriesLimit),
  });
  if (selectedTag) {
    pageParams.set("tag", selectedTag);
  }

  const fragmentUrl = `/d/${day}?view=tags&${fragmentParams.toString()}`;
  const pageUrl = `/d/${day}?${pageParams.toString()}`;

  state.sortKind = nextKind;
  state.sortDir = nextDir;
  updateSortButtons();
  refreshDetailLinksForSort(document);

  const target = list instanceof HTMLElement ? list : detail;
  if (!target) return;

  htmx.ajax("GET", fragmentUrl, {
    source: target,
    target,
    swap: "outerHTML",
    pushURL: pageUrl,
  });
};

const sync = (root = document) => {
  state.saveSuppressed = false;
  const hadTargetParam = new URLSearchParams(window.location.search).has("target");
  updateHeaderHeight();
  attachEntriesScrollListener();
  if (!state.query) {
    state.query = readStoredSearchQuery();
  }
  syncFromDetail(root);
  syncSummarySkeletons(root);
  void hydrateTagsViewSummary(root);
  buildSearchIndex(root);
  clearSearchForTargetNavigation();
  applySearch(state.query);
  syncFromDetail(root);
  animateDetailEntries(root);
  highlightRequestedTag(root);
  if (!hadTargetParam) {
    maybeRestoreEntriesAnchor();
  }
};

const captureListPositions = () => {
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

const animateListReorder = (onFinish) => {
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

const syncListOnly = (root = document) => {
  updateHeaderHeight();
  syncSortStateFromDom(root);
  buildSearchIndex(root);
  applySearch(state.query);
  refreshDetailLinksForSort(root);
  const selectedTag = getSelectedTrace(root);
  if (selectedTag) {
    setActiveTag(selectedTag, root, { behavior: "auto", scroll: false });
  }
  // Scroll deferred to htmx:afterSettle — runs after FLIP animation completes.
};

const syncDetailOnly = (root = document) => {
  syncFromDetail(root);
  syncSummarySkeletons(root);
  buildSearchIndex(root);
  applySearch(state.query);
  animateDetailEntries(root);
  highlightRequestedTag(root);
  void hydrateTagsViewSummary(root);
  if (state.pendingTagHighlight) {
    const selected = getSelectedTrace(root);
    if (selected && selected === state.pendingTagHighlight) {
      setActiveTag(selected, root, { behavior: "smooth" });
      const linkedRow = document.getElementById(`tag-index-${selected}`);
      if (linkedRow instanceof HTMLElement) {
        const listBody = findListBody(root);
        if (listBody instanceof HTMLElement) {
          scrollRowIntoView(linkedRow, listBody, "smooth");
        }
        flashHighlight(linkedRow);
      }
      state.pendingTagHighlight = "";
    }
  }
};

if (!globalThis[BOOT_KEY]) {
  globalThis[BOOT_KEY] = true;
  window.addEventListener("resize", updateHeaderHeight);
  initHeatmapTooltip();

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const clearBtn = target.closest("[data-tags-view-search-clear]");
    if (clearBtn instanceof HTMLButtonElement) {
      event.preventDefault();
      scheduleSearch("", { immediate: true });
      if (state.input instanceof HTMLInputElement) {
        state.input.value = "";
        state.input.focus();
      }
      return;
    }

    const entryLink = target.closest("#tags-view-detail .tags-view__entry-open");
    if (entryLink) {
      storeMainScrollTop();
      captureEntriesAnchor();
      return;
    }

    const row = target.closest("#tags-view-list .tags-view__index-row");
    if (row) {
      if (!state.saveSuppressed) {
        storeMainScrollTop();
        captureEntriesAnchor();
        state.saveSuppressed = true;
      }
      clearScrollTarget(null, { emitEvent: false });
      state.pendingDetailScrollTop = true;
      return;
    }

    const detailLink = target.closest(
      "#tags-view-detail .tags-view__related-link, #tags-view-detail .tags-view__entry-tag",
    );
    if (!(detailLink instanceof HTMLAnchorElement)) return;
    if (!state.saveSuppressed) {
      storeMainScrollTop();
      captureEntriesAnchor();
      state.saveSuppressed = true;
    }
    state.pendingDetailScrollTop = true;
  });

  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.matches("[data-tags-view-search]")) return;
    scheduleSearch(target.value);
  });

  document.addEventListener("search", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.matches("[data-tags-view-search]")) return;
    scheduleSearch(target.value, { immediate: true });
  });

  document.addEventListener("tags-view:navigate", (event) => {
    const tag = String(event?.detail?.tag || "").trim();
    if (!tag) return;
    state.pendingTagHighlight = tag;
    setActiveTag(tag, document, { behavior: "smooth" });
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    const summaryEl =
      target.closest?.(".tags-view__summary") ||
      (target.classList?.contains("tags-view__summary") ? target : null);
    if (summaryEl) {
      cacheTagsViewSummary(summaryEl);
    }
    const inList = target.closest?.("#tags-view-list");
    const inEntries = target.closest?.("[data-tags-view-entries]");
    if (target.id === "tags-view-list" || inList) {
      syncListOnly(document);
      return;
    }
    if (target.id === "tags-view-detail" || inEntries) {
      syncDetailOnly(document);
      return;
    }
    if (target.id === "main-content") {
      sync(document);
    }
  });

  document.body.addEventListener("htmx:configRequest", (event) => {
    if (event.detail?.verb !== "get") return;
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    if (target.id === "tags-view-list") {
      const selectedInput = document.getElementById("tags-view-selected-tag");
      const selected =
        String(selectedInput?.value || "").trim() || getSelectedTrace(document) || "";
      if (selected) {
        event.detail.parameters.tag = selected;
      }
      event.detail.parameters.view = "tags";
      return;
    }
    if (target.id !== "tags-view-detail") return;

    const path = event.detail?.path || "";
    let destTag = "";
    try {
      const url = new URL(path, window.location.origin);
      destTag = url.searchParams.get("tag") || "";
    } catch {
      return;
    }
    if (!destTag) return;

    const key = getTagsLocationKey(destTag);
    const map = readEntriesAnchorMap();
    const stored = map[key];
    if (stored?.entryId && stored?.tag === destTag) {
      event.detail.parameters.restore_entry = stored.entryId;
    }
  });

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    if (target.id === "tags-view-list" || target.closest?.("#tags-view-list")) {
      captureListPositions();
      return;
    }
    if (target.id !== "tags-view-detail") return;
    captureListPositions();
    if (!state.saveSuppressed) {
      storeMainScrollTop();
      captureEntriesAnchor();
      state.saveSuppressed = true;
    }
    state.restoreAppliedForLocation = "";
    if (!state.pendingDetailScrollTop) {
      state.pendingDetailScrollTop = true;
    }
  });

  document.body.addEventListener("htmx:afterSettle", (event) => {
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    if (target.id === "tags-view-list" || target.closest?.("#tags-view-list")) {
      animateListReorder(() => {
        scrollActiveRowIntoView(document, "smooth");
      });
    }
    if (target.id !== "tags-view-detail") return;
    if (!state.pendingDetailScrollTop) return;
    state.pendingDetailScrollTop = false;
    scrollMainContentTop();
    state.saveSuppressed = false;
    maybeRestoreEntriesAnchor();
  });

  document.addEventListener("app:rehydrate", (event) => {
    state.restoreAppliedForLocation = "";
    sync(event?.detail?.context || document);
  });
  document.addEventListener("app:view-changed", (event) => {
    if (event?.detail?.view === "tags") return;
    resetEntriesRestoreState();
  });
  document.addEventListener("app:teardown", () => {
    if (!state.saveSuppressed) {
      storeMainScrollTop();
      captureEntriesAnchor();
    }
  });
  window.addEventListener("pagehide", () => {
    if (!state.saveSuppressed) {
      storeMainScrollTop();
      captureEntriesAnchor();
    }
  });

  sync(document);
}
