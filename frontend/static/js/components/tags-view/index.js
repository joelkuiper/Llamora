import { clearScrollTarget, flashHighlight } from "../../ui.js";
import { state } from "./state.js";
import { findDetail } from "./dom.js";
import {
  getSelectedTrace,
  refreshDetailLinksForSort,
  syncFromDetail,
  updateSelectedTagCounts,
  animateDetailEntries,
  highlightRequestedTag,
} from "./detail.js";
import {
  applySearch,
  buildIndexListIfNeeded,
  buildSearchIndex,
  clearSearchForTargetNavigation,
  ensureActiveRowPresent,
  findRowByTagName,
  readStoredSearchQuery,
  requestSort,
  scheduleSearch,
  scrollActiveRowIntoView,
  setActiveTag,
  syncSortStateFromDom,
  updateSortButtons,
  hydrateIndexFromTemplate,
  captureListPositions,
  animateListReorder,
} from "./index-search.js";
import {
  applyStoredHeatmapOffset,
  handleHeatmapAfterSwap,
  handleHeatmapBeforeSwap,
  initHeatmapTooltip,
  storeHeatmapOffsetFromRoot,
} from "./heatmap.js";
import {
  attachEntriesScrollListener,
  captureEntriesAnchor,
  getStoredEntriesAnchor,
  maybeRestoreEntriesAnchor,
  resetEntriesRestoreState,
  scrollMainContentTop,
  storeMainScrollTop,
} from "./scroll.js";
import { cacheTagsViewSummary, hydrateTagsViewSummary, syncSummarySkeletons } from "./summary.js";

const BOOT_KEY = "__llamoraTagsViewBooted";

const updateHeaderHeight = () => {
  const header = document.getElementById("app-header");
  if (!header) return;
  const height = Math.ceil(header.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--app-header-height", `${height}px`);
};

const sync = (root = document) => {
  state.saveSuppressed = false;
  const hadTargetParam = new URLSearchParams(window.location.search).has("target");
  updateHeaderHeight();
  attachEntriesScrollListener();
  if (!state.query) {
    state.query = readStoredSearchQuery();
  }
  hydrateIndexFromTemplate(root);
  buildIndexListIfNeeded(root);
  syncFromDetail(root, { ensureActiveRowPresent, setActiveTag });
  syncSummarySkeletons(root);
  void hydrateTagsViewSummary(root);
  buildSearchIndex(root);
  clearSearchForTargetNavigation();
  applySearch(state.query);
  syncFromDetail(root, { ensureActiveRowPresent, setActiveTag });
  applyStoredHeatmapOffset(root);
  animateDetailEntries(root);
  highlightRequestedTag(root, { setActiveTag });
  if (!hadTargetParam) {
    maybeRestoreEntriesAnchor();
  }
};

const syncListOnly = (root = document) => {
  updateHeaderHeight();
  syncSortStateFromDom(root);
  updateSortButtons(root);
  hydrateIndexFromTemplate(root);
  buildIndexListIfNeeded(root);
  buildSearchIndex(root);
  applySearch(state.query);
  refreshDetailLinksForSort(root);
  const pending = state.pendingTagHighlight;
  if (pending && findRowByTagName(pending)) {
    setActiveTag(pending, root, { behavior: "smooth", scroll: true });
    state.pendingTagHighlight = "";
  } else {
    const selectedTag = getSelectedTrace(root);
    if (selectedTag) {
      ensureActiveRowPresent(selectedTag);
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
  applyStoredHeatmapOffset(root);
  animateDetailEntries(root);
  highlightRequestedTag(root, { setActiveTag });
  void hydrateTagsViewSummary(root);
  if (state.pendingTagHighlight) {
    const selected = getSelectedTrace(root);
    if (selected && selected === state.pendingTagHighlight) {
      setActiveTag(selected, root, { behavior: "smooth" });
      const linkedRow = document.getElementById(`tag-index-${selected}`);
      if (linkedRow instanceof HTMLElement) {
        scrollActiveRowIntoView(root, "smooth");
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
      "#tags-view-detail .tags-view__related-link, #tags-view-detail .tags-view__entry-tag, #tags-view-detail .entry-tag .tag-label",
    );
    if (!(detailLink instanceof HTMLElement)) return;
    if (detailLink.closest?.(".tag-remove")) {
      return;
    }
    const tagName = String(detailLink.dataset?.tagName || "").trim();
    if (tagName) {
      state.pendingTagHighlight = tagName;
      if (findRowByTagName(tagName)) {
        setActiveTag(tagName, document, { behavior: "smooth" });
      } else {
        ensureActiveRowPresent(tagName);
      }
    }
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
    ensureActiveRowPresent(tag);
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
    if (!detailRoot || document.getElementById("main-content")?.dataset.view !== "tags") {
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
    storeHeatmapOffsetFromRoot(target);
    if (target.id === "tags-view-list") {
      animateListReorder(() => {
        if (state.pendingListScroll) {
          state.pendingListScroll = false;
          scrollActiveRowIntoView(document, "smooth");
        }
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
    storeHeatmapOffsetFromRoot();
  });
  window.addEventListener("pagehide", () => {
    if (!state.saveSuppressed) {
      storeMainScrollTop();
      captureEntriesAnchor();
    }
    storeHeatmapOffsetFromRoot();
  });

  sync(document);
}
