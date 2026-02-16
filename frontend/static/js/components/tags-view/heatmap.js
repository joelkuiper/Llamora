import { cacheLoader } from "../../services/cache-loader.js";
import { sessionStore } from "../../utils/storage.js";
import { transitionHide, transitionShow } from "../../utils/transition.js";

const heatmapState = {
  initialized: false,
  tooltip: null,
  dateEl: null,
  summaryEl: null,
  timer: null,
  hideTimer: null,
  activeCell: null,
  activeDate: "",
  cache: new Map(),
  fetchController: null,
  intent: 0,
};

const HEATMAP_STORAGE_KEY = "tags:heatmap";

const makeDaySummaryKey = (date) => `day:${String(date || "").trim()}`;

const readHeatmapOffsetMap = () => sessionStore.get(HEATMAP_STORAGE_KEY) ?? {};

const writeHeatmapOffsetMap = (map) => {
  sessionStore.set(HEATMAP_STORAGE_KEY, map);
};

const normalizeHeatmapOffset = (value) => {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
};

const getHeatmapElement = (root = document) =>
  root.querySelector?.(".tags-view__heatmap") || document.querySelector(".tags-view__heatmap");

const getHeatmapTagHash = (heatmap) => String(heatmap?.dataset?.heatmapTag || "").trim();

const getHeatmapOffset = (heatmap) => normalizeHeatmapOffset(heatmap?.dataset?.heatmapOffset);

const storeHeatmapOffset = (tagHash, offset) => {
  if (!tagHash || !Number.isFinite(offset)) return;
  const map = readHeatmapOffsetMap();
  map[tagHash] = {
    offset,
    updatedAt: Date.now(),
  };
  const entries = Object.entries(map);
  if (entries.length > 60) {
    entries
      .sort((a, b) => Number(b[1]?.updatedAt || 0) - Number(a[1]?.updatedAt || 0))
      .slice(60)
      .forEach(([oldKey]) => {
        delete map[oldKey];
      });
  }
  writeHeatmapOffsetMap(map);
};

export const findHeatmapInRoot = (root = document) => {
  if (!(root instanceof Element)) {
    return getHeatmapElement();
  }
  if (root.classList?.contains("tags-view__heatmap")) return root;
  return root.querySelector?.(".tags-view__heatmap") || root.closest?.(".tags-view__heatmap");
};

const storeHeatmapOffsetFromElement = (heatmap) => {
  if (!(heatmap instanceof HTMLElement)) return;
  const tagHash = getHeatmapTagHash(heatmap);
  const offset = getHeatmapOffset(heatmap);
  if (!tagHash || offset == null) return;
  storeHeatmapOffset(tagHash, offset);
};

export const storeHeatmapOffsetFromRoot = (root = document) => {
  const heatmap = findHeatmapInRoot(root);
  if (heatmap instanceof HTMLElement) {
    storeHeatmapOffsetFromElement(heatmap);
  }
};

const readStoredHeatmapOffset = (tagHash) => {
  if (!tagHash) return null;
  const map = readHeatmapOffsetMap();
  const value = map[tagHash];
  if (!value || typeof value !== "object") return null;
  return normalizeHeatmapOffset(value.offset);
};

export const applyStoredHeatmapOffset = (root = document) => {
  const heatmap = getHeatmapElement(root);
  if (!(heatmap instanceof HTMLElement)) return;
  const tagHash = getHeatmapTagHash(heatmap);
  if (!tagHash) return;
  const currentOffset = getHeatmapOffset(heatmap);
  const storedOffset = readStoredHeatmapOffset(tagHash);
  if (storedOffset == null || storedOffset === currentOffset) return;

  const hxGet = heatmap.getAttribute("hx-get");
  if (!hxGet) return;
  let url;
  try {
    url = new URL(hxGet, window.location.origin);
  } catch {
    return;
  }
  url.searchParams.set("heatmap_offset", String(storedOffset));
  htmx.ajax("GET", `${url.pathname}${url.search}`, {
    source: heatmap,
    target: heatmap,
    swap: "outerHTML",
  });
};

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
  cancelHeatmapFetch();
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

const nextHeatmapIntent = () => {
  heatmapState.intent += 1;
  return heatmapState.intent;
};

const cancelHeatmapFetch = () => {
  heatmapState.fetchController?.abort();
  heatmapState.fetchController = null;
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

const fetchHeatmapSummary = async (date, { signal } = {}) => {
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
  return fetch(`/d/${date}/summary`, {
    signal,
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
      return summary;
    })
    .catch(() => "");
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
  cancelHeatmapFetch();
  heatmapState.activeCell = cell;
  heatmapState.activeDate = date;
  const intent = nextHeatmapIntent();
  heatmapState.timer = window.setTimeout(async () => {
    if (heatmapState.activeDate !== date || heatmapState.intent !== intent) return;
    const tooltip = ensureHeatmapTooltip();
    const label = String(cell.dataset.heatmapLabel || cell.getAttribute("aria-label") || "").trim();
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

    const fetchController = new AbortController();
    heatmapState.fetchController = fetchController;
    const summary = await fetchHeatmapSummary(date, {
      signal: fetchController.signal,
    });
    if (heatmapState.fetchController === fetchController) {
      heatmapState.fetchController = null;
    }
    if (heatmapState.activeDate !== date || heatmapState.intent !== intent) return;
    if (heatmapState.summaryEl) {
      const text = summary || "Summary unavailable right now.";
      heatmapState.summaryEl.innerHTML = `<p class="summary-fade">${text}</p>`;
    }
    positionHeatmapTooltip(tooltip, cell);
  }, 420);
};

export const initHeatmapTooltip = () => {
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

export const handleHeatmapBeforeSwap = (target, requestConfig) => {
  if (!(target instanceof Element)) return;
  if (!target.classList?.contains("tags-view__heatmap")) return;
  const elt = requestConfig?.elt;
  if (!(elt instanceof Element) || !elt.classList.contains("tags-view__heatmap-btn")) {
    target.classList.add("no-animate");
  }
};

export const handleHeatmapAfterSwap = (target, requestConfig) => {
  if (!(target instanceof Element)) return;
  if (!target.classList?.contains("tags-view__heatmap")) return;
  const elt = requestConfig?.elt;
  if (!(elt instanceof Element) || !elt.classList.contains("tags-view__heatmap-btn")) {
    const fresh = document.querySelector(".tags-view__heatmap");
    if (fresh) fresh.classList.add("no-animate");
  }
};
