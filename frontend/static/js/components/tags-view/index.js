import { getFrameState } from "../../services/app-state.js";
import { registerHydrationOwner } from "../../services/hydration-owners.js";
import { applyTagsCatalogCountUpdate } from "../../services/tags-catalog.js";
import { clearScrollTarget, flashHighlight } from "../../ui.js";
import {
  animateDetailEntries,
  getSelectedTrace,
  refreshDetailLinksForNav,
  setSelectedTagCount,
  syncFromDetail,
  updateSelectedTagCounts,
} from "./detail.js";
import { findDetail } from "./dom.js";
import {
  applyStoredHeatmapOffset,
  handleHeatmapAfterSwap,
  handleHeatmapBeforeSwap,
  initHeatmapTooltip,
  storeHeatmapOffsetFromRoot,
} from "./heatmap.js";
import {
  animateListReorder,
  applySearch,
  buildIndexListIfNeeded,
  buildSearchIndex,
  captureListPositions,
  ensureActiveRowPresent,
  findRowByTagName,
  hydrateIndexFromTemplate,
  readStoredSearchQuery,
  rebuildIndexList,
  requestSort,
  scheduleSearch,
  scrollActiveRowIntoView,
  setActiveTag,
  syncSortStateFromDom,
  updateSortButtons,
} from "./index-search.js";
import {
  cancelEntriesAnchorRestore,
  captureEntriesAnchor,
  getStoredEntriesAnchor,
  maybeRestoreEntriesAnchor,
  registerTagsScrollStrategy,
  resetEntriesRestoreState,
  retryAnchorRestore,
  scrollMainContentTop,
  storeMainScrollTop,
} from "./scroll.js";
import {
  clearPendingDetailScroll,
  clearPendingTagHighlight,
  consumePendingDetailScroll,
  consumePendingListScroll,
  forcePhase,
  getPendingTagHighlight,
  isSaveSuppressed,
  requestDetailScroll,
  resetRestoreAppliedLocation,
  setPendingTagHighlight,
  setSaveSuppressed,
  state,
  TagsViewPhase,
  transitionPhase,
} from "./state.js";
import { cacheTagsViewSummary, hydrateTagsViewSummary, syncSummarySkeletons } from "./summary.js";

const isEntriesNavigationRequest = (event) => {
  const requestConfig = event?.detail?.requestConfig;
  const verb = String(requestConfig?.verb || "")
    .trim()
    .toLowerCase();
  if (verb !== "get") return false;
  const path = String(requestConfig?.path || "").trim();
  if (!path) return false;
  return path.includes("/fragments/tags/") && path.includes("/detail/");
};

const updateHeaderHeight = () => {
  const header = document.getElementById("app-header");
  if (!header) return;
  const height = Math.ceil(header.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--app-header-height", `${height}px`);
};

const escapeSelectorValue = (value) => {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return String(value).replaceAll('"', '\\"');
};

const removeEntryItemWithAnimation = (entryItem, durationMs = 200) => {
  if (!(entryItem instanceof HTMLElement)) return;
  if (entryItem.dataset.removing === "true") return;
  entryItem.dataset.removing = "true";
  entryItem.classList.add("motion-animate-entry-delete");
  const leadEntry = entryItem.querySelector(".tags-view__entry");
  if (leadEntry instanceof HTMLElement) {
    leadEntry.classList.add("motion-animate-entry-delete");
  }

  let sibling = entryItem.previousElementSibling;
  while (sibling && !sibling.classList.contains("tags-view__entries-divider")) {
    sibling = sibling.previousElementSibling;
  }
  const divider =
    sibling instanceof HTMLElement && sibling.classList.contains("tags-view__entries-divider")
      ? sibling
      : null;

  window.setTimeout(() => {
    if (entryItem.isConnected) {
      entryItem.remove();
    }
    if (!divider?.isConnected) return;
    let next = divider.nextElementSibling;
    let hasEntries = false;
    while (next && !next.classList.contains("tags-view__entries-divider")) {
      if (next.classList.contains("tags-view__entry-item")) {
        hasEntries = true;
        break;
      }
      next = next.nextElementSibling;
    }
    if (!hasEntries) {
      divider.remove();
    }
  }, durationMs);
};

const applyTagCountUpdate = (payload, root = document) => {
  if (!payload || typeof payload !== "object") return;
  const tagName = String(payload.tag || "").trim();
  if (!tagName) return;
  const rawCount = Number.parseInt(String(payload.count ?? ""), 10);
  if (!Number.isFinite(rawCount)) return;
  const count = Math.max(0, rawCount);
  const tagHash = String(payload.tag_hash || "").trim();
  const action = String(payload.action || "")
    .trim()
    .toLowerCase();
  const entryId = String(payload.entry_id || "").trim();

  const listRoot = document.getElementById("tags-view-list");
  if (!listRoot) return;

  hydrateIndexFromTemplate(root);
  const idx = Array.isArray(state.indexItems)
    ? state.indexItems.findIndex((item) => item?.name === tagName)
    : -1;
  const row = findRowByTagName(tagName);
  const shouldRebuild = !row || count <= 0;
  if (shouldRebuild) {
    state.listBuilt = false;
    rebuildIndexList(root);
    const activeTag = getSelectedTrace(root);
    if (activeTag) {
      setActiveTag(activeTag, root, { behavior: "auto", scroll: false });
    }
  } else if (row instanceof HTMLElement) {
    const countEl = row.querySelector(".tags-view__index-count");
    if (countEl) {
      countEl.textContent = String(count);
    }
    row.dataset.tagsCount = String(count);
    if (idx >= 0 && Array.isArray(state.indexItems)) {
      state.indexItems[idx] = {
        ...state.indexItems[idx],
        count,
        hash: tagHash || state.indexItems[idx].hash,
      };
    }
  }

  const detail = findDetail(root);
  const selectedTag = String(detail?.dataset?.selectedTag || "").trim();
  if (selectedTag && selectedTag === tagName) {
    setSelectedTagCount(root, count);
    if (action === "remove" && entryId) {
      const escapedId = escapeSelectorValue(entryId);
      const entryItem = document.querySelector(
        `.tags-view__entry-item[data-entry-id="${escapedId}"]`,
      );
      removeEntryItemWithAnimation(entryItem, 200);
    }
  }
};

const ensureActiveTagVisible = (root = document) => {
  if (!state.query) return;
  const activeTag = getSelectedTrace(root);
  if (!activeTag) return;
  const row = findRowByTagName(activeTag);
  if (!(row instanceof HTMLElement)) return;
  if (!row.classList.contains("is-filtered-out")) return;
  scheduleSearch("", { immediate: true });
};

const sync = (root = document) => {
  setSaveSuppressed(false);
  forcePhase(TagsViewPhase.IDLE, "sync");
  updateHeaderHeight();
  syncSortStateFromDom(root);
  updateSortButtons(root);
  if (!state.query) {
    state.query = readStoredSearchQuery();
  }
  hydrateIndexFromTemplate(root);
  buildIndexListIfNeeded(root);
  syncFromDetail(root, { ensureActiveRowPresent, setActiveTag });
  syncSummarySkeletons(root);
  void hydrateTagsViewSummary(root);
  buildSearchIndex(root);
  applySearch(state.query);
  syncFromDetail(root, { ensureActiveRowPresent, setActiveTag });
  applyStoredHeatmapOffset(root);
  animateDetailEntries(root);
  ensureActiveTagVisible(root);
  maybeRestoreEntriesAnchor();
};

const syncListOnly = (root = document) => {
  updateHeaderHeight();
  syncSortStateFromDom(root);
  updateSortButtons(root);
  hydrateIndexFromTemplate(root);
  buildIndexListIfNeeded(root);
  buildSearchIndex(root);
  applySearch(state.query);
  ensureActiveTagVisible(root);
  refreshDetailLinksForNav(root);
  const pending = getPendingTagHighlight();
  if (pending && findRowByTagName(pending)) {
    setActiveTag(pending, root, { behavior: "smooth", scroll: true });
    clearPendingTagHighlight();
  } else {
    const selectedTag = getSelectedTrace(root);
    const selectedTagHash = String(findDetail(root)?.dataset?.selectedTagHash || "").trim();
    if (selectedTag) {
      ensureActiveRowPresent(selectedTag, { tagHash: selectedTagHash });
      setActiveTag(selectedTag, root, { behavior: "auto", scroll: false });
    } else if (state.rows.length) {
      const fallbackTag = state.rows[0]?.dataset?.tagName || "";
      if (fallbackTag) {
        setActiveTag(fallbackTag, root, { behavior: "auto", scroll: false });
      }
    }
  }
  // Scroll deferred to htmx:afterSettle — runs after FLIP animation completes.
};

const syncDetailOnly = (root = document) => {
  syncFromDetail(root, { ensureActiveRowPresent, setActiveTag });
  syncSummarySkeletons(root);
  hydrateIndexFromTemplate(root);
  buildIndexListIfNeeded(root);
  buildSearchIndex(root);
  applySearch(state.query);
  ensureActiveTagVisible(root);
  applyStoredHeatmapOffset(root);
  animateDetailEntries(root);
  void hydrateTagsViewSummary(root);
  const pending = getPendingTagHighlight();
  if (pending) {
    const selected = getSelectedTrace(root);
    if (selected && selected === pending) {
      setActiveTag(selected, root, { behavior: "smooth" });
      const linkedRow = document.getElementById(`tag-index-${selected}`);
      if (linkedRow instanceof HTMLElement) {
        scrollActiveRowIntoView(root, "smooth");
        flashHighlight(linkedRow);
      }
      clearPendingTagHighlight();
    }
  }
};

window.addEventListener("resize", updateHeaderHeight);
initHeatmapTooltip();
registerTagsScrollStrategy();

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;

  const sortBtn = target.closest?.("[data-tags-sort-kind][data-tags-sort-dir]");
  if (sortBtn instanceof HTMLElement) {
    event.preventDefault();
    const kind = sortBtn.dataset.tagsSortKind;
    const dir = sortBtn.dataset.tagsSortDir;
    requestSort(kind, dir);
    return;
  }

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
    transitionPhase(TagsViewPhase.NAVIGATING, "click:index-row");
    if (!isSaveSuppressed()) {
      storeMainScrollTop();
      captureEntriesAnchor();
      setSaveSuppressed(true);
    }
    clearScrollTarget(null, { emitEvent: false });
    requestDetailScroll();
    return;
  }

  const detailLink = target.closest(
    "#tags-view-detail .tags-view__related-link, #tags-view-detail .tags-view__entry-tag, #tags-view-detail .entry-tag .tag-label",
  );
  if (!(detailLink instanceof HTMLElement)) return;
  if (detailLink.closest?.(".tag-remove")) {
    return;
  }
  const tagName = String(detailLink.dataset?.tagName || "").trim();
  const tagHash =
    String(detailLink.dataset?.tagHash || "").trim() ||
    String(detailLink.closest?.(".entry-tag")?.dataset?.tagHash || "").trim();
  if (tagName) {
    setPendingTagHighlight(tagName);
    if (findRowByTagName(tagName)) {
      setActiveTag(tagName, document, { behavior: "smooth" });
    } else {
      ensureActiveRowPresent(tagName, { tagHash });
    }
  }
  transitionPhase(TagsViewPhase.NAVIGATING, "click:detail-tag-link");
  if (!isSaveSuppressed()) {
    storeMainScrollTop();
    captureEntriesAnchor();
    setSaveSuppressed(true);
  }
  requestDetailScroll();
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
  const tagHash = String(event?.detail?.tagHash || "").trim();
  if (!tag) return;
  transitionPhase(TagsViewPhase.NAVIGATING, "event:tags-view-navigate");
  setPendingTagHighlight(tag);
  ensureActiveRowPresent(tag, { tagHash });
  setActiveTag(tag, document, { behavior: "smooth" });
});

document.body.addEventListener("tags:tag-count-updated", (event) => {
  applyTagsCatalogCountUpdate(event?.detail || {}, document);
  if (getFrameState().view !== "tags") return;
  applyTagCountUpdate(event?.detail || {}, document);
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail?.target;
  if (!(target instanceof Element)) return;
  const summaryEl =
    target.closest?.(".tags-view__summary") ||
    (target.classList?.contains("tags-view__summary") ? target : null);
  if (summaryEl) {
    cacheTagsViewSummary(summaryEl);
    retryAnchorRestore();
  }
  const inList = target.closest?.("#tags-view-list");
  const inEntries = target.closest?.("[data-tags-view-entries]");
  if (target.id === "tags-view-list" || inList) {
    transitionPhase(TagsViewPhase.SETTLING_LIST, "afterSwap:list");
    syncListOnly(document);
    return;
  }
  if (target.id === "tags-view-detail" || inEntries) {
    transitionPhase(TagsViewPhase.SETTLING_DETAIL, "afterSwap:detail");
    syncDetailOnly(document);
    if (inEntries && target.id !== "tags-view-detail" && isEntriesNavigationRequest(event)) {
      retryAnchorRestore();
    }
    return;
  }
  if (target.id === "main-content") {
    sync(document);
  }
});

document.body.addEventListener("htmx:beforeSwap", (event) => {
  const target = event.detail?.target;
  if (!(target instanceof Element)) return;
  if (!target.classList?.contains("tags-view__heatmap")) return;
  handleHeatmapBeforeSwap(target, event.detail?.requestConfig);
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail?.target;
  if (!(target instanceof Element)) return;
  if (!target.classList?.contains("tags-view__heatmap")) return;
  handleHeatmapAfterSwap(target, event.detail?.requestConfig);
});

document.body.addEventListener("htmx:afterRequest", (event) => {
  const detailRoot = findDetail(document);
  if (!detailRoot || getFrameState().view !== "tags") {
    return;
  }
  const xhr = event.detail?.xhr;
  if (xhr && (xhr.status < 200 || xhr.status >= 300)) return;
  const requestConfig = event.detail?.requestConfig;
  const path = String(requestConfig?.path || "");
  if (requestConfig?.verb !== "delete" || !path.includes("/e/entry/")) return;
  const target = event.detail?.target || event.detail?.elt;
  const entry = (target instanceof Element && (target.closest?.(".entry") || target)) || null;
  if (entry instanceof Element && entry.classList.contains("assistant")) return;
  updateSelectedTagCounts(document, -1);

  // Clean up the orphaned wrapper <li> and day-divider after the entry's
  // delete animation completes (swap:180ms delay). We capture the elements
  // now while the target is still connected, then schedule removal after the
  // animation.
  const entryItem = target instanceof Element ? target.closest?.(".tags-view__entry-item") : null;
  if (entryItem instanceof HTMLElement) {
    let sib = entryItem.previousElementSibling;
    while (sib && !sib.classList.contains("tags-view__entries-divider")) {
      sib = sib.previousElementSibling;
    }
    const divider =
      sib instanceof HTMLElement && sib.classList.contains("tags-view__entries-divider")
        ? sib
        : null;

    setTimeout(() => {
      entryItem.remove();
      if (divider?.isConnected) {
        let next = divider.nextElementSibling;
        let hasEntries = false;
        while (next && !next.classList.contains("tags-view__entries-divider")) {
          if (next.classList.contains("tags-view__entry-item")) {
            hasEntries = true;
            break;
          }
          next = next.nextElementSibling;
        }
        if (!hasEntries) divider.remove();
      }
    }, 200);
  }
});

document.body.addEventListener("htmx:responseError", (event) => {
  const target = event.detail?.target;
  if (!(target instanceof Element)) return;
  if (target.id !== "tags-view-detail") return;
  setSaveSuppressed(false);
  clearPendingDetailScroll();
  forcePhase(TagsViewPhase.IDLE, "responseError:detail");
});

document.body.addEventListener("htmx:configRequest", (event) => {
  if (event.detail?.verb !== "get") return;
  const target = event.detail?.target;
  if (!(target instanceof Element)) return;
  if (target.id === "tags-view-list") {
    const selectedInput = document.getElementById("tags-view-selected-tag");
    const selected = String(selectedInput?.value || "").trim() || getSelectedTrace(document) || "";
    if (selected) {
      event.detail.parameters.tag = selected;
    }
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

  const stored = getStoredEntriesAnchor(destTag);
  if (stored?.entryId && stored?.tag === destTag) {
    event.detail.parameters.restore_entry = stored.entryId;
  }
});

document.body.addEventListener("htmx:beforeRequest", (event) => {
  const target = event.detail?.target;
  if (!(target instanceof Element)) return;
  const requestConfig = event.detail?.requestConfig;
  const path = String(requestConfig?.path || "");
  const inEntriesRegion = Boolean(target.closest?.("[data-tags-view-entries]"));
  const isEntryOrTagMutationPath = path.startsWith("/e/") || path.startsWith("/t/");

  if ((inEntriesRegion || isEntryOrTagMutationPath) && !isEntriesNavigationRequest(event)) {
    cancelEntriesAnchorRestore();
  }

  if (target.id === "tags-view-list" || target.closest?.("#tags-view-list")) {
    transitionPhase(TagsViewPhase.LOADING_LIST, "beforeRequest:list");
    captureListPositions();
    return;
  }
  if (target.id !== "tags-view-detail") return;
  transitionPhase(TagsViewPhase.LOADING_DETAIL, "beforeRequest:detail");
  captureListPositions();
  if (!isSaveSuppressed()) {
    storeMainScrollTop();
    captureEntriesAnchor();
    setSaveSuppressed(true);
  }
  resetRestoreAppliedLocation();
  requestDetailScroll();
});

document.body.addEventListener("htmx:afterSettle", (event) => {
  const target = event.detail?.target;
  if (!(target instanceof Element)) return;
  storeHeatmapOffsetFromRoot(target);
  if (target.id === "tags-view-list") {
    animateListReorder(() => {
      if (consumePendingListScroll()) {
        scrollActiveRowIntoView(document, "smooth");
      }
    });
    if (!transitionPhase(TagsViewPhase.IDLE, "afterSettle:list")) {
      forcePhase(TagsViewPhase.IDLE, "afterSettle:list:force");
    }
  }
  if (target.id !== "tags-view-detail") return;
  if (!consumePendingDetailScroll()) return;
  scrollMainContentTop();
  setSaveSuppressed(false);
  maybeRestoreEntriesAnchor();
  if (!transitionPhase(TagsViewPhase.IDLE, "afterSettle:detail")) {
    forcePhase(TagsViewPhase.IDLE, "afterSettle:detail:force");
  }
});

registerHydrationOwner({
  id: "tags-view",
  selector: "#tags-view",
  hydrate: (context) => {
    resetRestoreAppliedLocation();
    const root = context instanceof Element ? context : document;
    sync(root);
  },
  teardown: () => {
    if (!isSaveSuppressed()) {
      storeMainScrollTop();
      captureEntriesAnchor();
    }
    storeHeatmapOffsetFromRoot();
  },
});
document.addEventListener("app:view-changed", (event) => {
  if (event?.detail?.view === "tags") return;
  resetEntriesRestoreState();
});
window.addEventListener("pagehide", () => {
  if (!isSaveSuppressed()) {
    storeMainScrollTop();
    captureEntriesAnchor();
  }
  storeHeatmapOffsetFromRoot();
});

sync(document);
