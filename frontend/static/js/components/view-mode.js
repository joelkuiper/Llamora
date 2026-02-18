import { createPopover } from "../popover.js";
import { registerHydrationOwner } from "../services/hydration-owners.js";
import { getViewState, hydrateViewState } from "../services/view-state.js";
import { createListenerBag } from "../utils/events.js";
import { sessionStore } from "../utils/storage.js";
import { getActiveDay } from "./entries-view/active-day-store.js";

let currentInstance = null;
const TAGS_CONTEXT_KEY = "tags:last-context";
const TAGS_SORT_KEY = "tags:sort";
const ISO_DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

const normalizeDay = (value) => {
  const day = String(value || "").trim();
  return ISO_DAY_RE.test(day) ? day : "";
};

const normalizeSortKind = (value) =>
  String(value || "")
    .trim()
    .toLowerCase() === "alpha"
    ? "alpha"
    : "count";

const normalizeSortDir = (value) =>
  String(value || "")
    .trim()
    .toLowerCase() === "asc"
    ? "asc"
    : "desc";

const readStoredTagsContext = () => {
  const raw = sessionStore.get(TAGS_CONTEXT_KEY);
  if (!raw || typeof raw !== "object") return null;
  const selectedTag = String(raw.selectedTag || "").trim();
  const sortKind = normalizeSortKind(raw.sortKind);
  const sortDir = normalizeSortDir(raw.sortDir);
  const day = normalizeDay(raw.day);
  return {
    selectedTag,
    sortKind,
    sortDir,
    day,
  };
};

const readStoredTagsSort = () => {
  const raw = sessionStore.get(TAGS_SORT_KEY);
  if (!raw || typeof raw !== "object") return null;
  return {
    sortKind: normalizeSortKind(raw.sortKind),
    sortDir: normalizeSortDir(raw.sortDir),
  };
};

const writeStoredTagsContext = (context) => {
  if (!context || typeof context !== "object") return;
  sessionStore.set(TAGS_CONTEXT_KEY, {
    selectedTag: String(context.selectedTag || "").trim(),
    sortKind: normalizeSortKind(context.sortKind),
    sortDir: normalizeSortDir(context.sortDir),
    day: normalizeDay(context.day),
  });
};

const writeStoredTagsSort = (sortKind, sortDir) => {
  sessionStore.set(TAGS_SORT_KEY, {
    sortKind: normalizeSortKind(sortKind),
    sortDir: normalizeSortDir(sortDir),
  });
};

const resolveCurrentDay = () => {
  const viewState = getViewState();
  const view = String(viewState?.view || "").trim();
  const stateDay = normalizeDay(viewState?.day);
  if (view === "tags") {
    return stateDay;
  }
  return normalizeDay(getActiveDay()) || stateDay;
};

const buildDiaryUrl = (day) => {
  const resolved = normalizeDay(day);
  return resolved ? `/d/${resolved}` : "/d/today";
};

const buildTagsUrl = ({ selectedTag = "", day = "" } = {}) => {
  const cleanTag = String(selectedTag || "").trim();
  const cleanDay = normalizeDay(day);
  const path = cleanTag ? `/t/${encodeURIComponent(cleanTag)}` : "/t";
  const params = new URLSearchParams();
  if (cleanDay) {
    params.set("day", cleanDay);
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
};

const getLiveTagsContext = () => {
  const viewState = getViewState();
  const stored = readStoredTagsContext();
  const storedSort = readStoredTagsSort();
  const selectedTag =
    String(viewState?.view === "tags" ? viewState?.selected_tag : "").trim() ||
    String(stored?.selectedTag || "").trim();
  const sortKind = normalizeSortKind(storedSort?.sortKind || stored?.sortKind);
  const sortDir = normalizeSortDir(storedSort?.sortDir || stored?.sortDir);
  const day = resolveCurrentDay() || normalizeDay(stored?.day);
  const context = { selectedTag, sortKind, sortDir, day };
  writeStoredTagsContext(context);
  writeStoredTagsSort(context.sortKind, context.sortDir);
  return context;
};

const persistTagsContextFromState = () => {
  const viewState = getViewState();
  if (String(viewState?.view || "").trim() !== "tags") {
    return;
  }
  const storedSort = readStoredTagsSort();
  const sortKind = normalizeSortKind(storedSort?.sortKind);
  const sortDir = normalizeSortDir(storedSort?.sortDir);
  writeStoredTagsContext({
    selectedTag: String(viewState?.selected_tag || "").trim(),
    sortKind,
    sortDir,
    day: resolveCurrentDay() || normalizeDay(viewState?.day),
  });
};

const persistTagsSort = (detail) => {
  const kind = normalizeSortKind(detail?.sortKind);
  const dir = normalizeSortDir(detail?.sortDir);
  writeStoredTagsSort(kind, dir);
  const existing = readStoredTagsContext() || {};
  writeStoredTagsContext({
    ...existing,
    sortKind: kind,
    sortDir: dir,
    day: resolveCurrentDay() || existing.day,
  });
};

const destroyCurrentInstance = () => {
  if (!currentInstance) return;
  currentInstance.listenerBag.abort();
  currentInstance.controller.destroy();
  currentInstance = null;
};

const initViewMode = (root = document) => {
  const toggle =
    root.querySelector?.("#view-mode-toggle") || document.querySelector("#view-mode-toggle");
  const popover =
    root.querySelector?.("#view-mode-popover") || document.querySelector("#view-mode-popover");
  const panel = popover?.querySelector?.(".view-mode-panel");
  if (!toggle || !popover || !panel) return;

  if (currentInstance?.toggle && !currentInstance.toggle.isConnected) {
    destroyCurrentInstance();
  }

  if (currentInstance?.toggle === toggle) {
    currentInstance.syncFromContent();
    return;
  }

  destroyCurrentInstance();

  const listenerBag = createListenerBag();
  const controller = createPopover(toggle, popover, {
    animation: null,
    getPanel: () => panel,
    onShow: () => {
      toggle.classList.add("active");
      toggle.setAttribute("aria-expanded", "true");
      popover.classList.add("open");
    },
    onHide: () => {
      toggle.classList.remove("active");
      toggle.setAttribute("aria-expanded", "false");
      popover.classList.remove("open");
    },
    onHidden: () => {
      toggle.focus({ preventScroll: true });
    },
  });

  const setActive = (view) => {
    toggle.classList.remove("view-mode-diary", "view-mode-tags", "view-mode-structure");
    toggle.classList.add(`view-mode-${view}`);
    panel.querySelectorAll(".view-mode-option").forEach((option) => {
      const mode = String(option.dataset?.viewMode || "").trim();
      const isActive = mode ? mode === view : false;
      option.classList.toggle("is-active", Boolean(isActive));
      if (isActive) {
        option.setAttribute("aria-current", "true");
      } else {
        option.removeAttribute("aria-current");
      }
    });
  };

  const syncFromContent = () => {
    hydrateViewState(document);
    const view = String(getViewState()?.view || "diary").trim() || "diary";
    setActive(view);
    persistTagsContextFromState();
  };

  listenerBag.add(panel, "click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest(".view-mode-option")) {
      controller.hide();
    }
  });

  listenerBag.add(document, "keydown", (event) => {
    if (event.key === "Escape") {
      controller.hide();
    }
  });

  listenerBag.add(document, "app:view-changed", () => {
    syncFromContent();
    controller.hide();
  });

  listenerBag.add(document, "app:view-state-changed", () => {
    persistTagsContextFromState();
  });

  listenerBag.add(document, "tags:sort-changed", (event) => {
    persistTagsSort(event?.detail || {});
  });

  listenerBag.add(document.body, "htmx:configRequest", (event) => {
    const source = event?.detail?.elt;
    if (!(source instanceof Element)) return;
    const option = source.closest(".view-mode-option[data-view-mode]");
    if (!(option instanceof HTMLElement) || !panel.contains(option)) return;

    const mode = String(option.dataset.viewMode || "").trim();
    if (mode === "diary") {
      event.detail.path = buildDiaryUrl(resolveCurrentDay());
      return;
    }
    if (mode === "tags") {
      const tagsContext = getLiveTagsContext();
      event.detail.path = buildTagsUrl(tagsContext);
    }
  });

  toggle.addEventListener("click", () => {
    if (controller.isOpen) {
      controller.hide();
    } else {
      controller.show();
    }
  });

  syncFromContent();

  currentInstance = {
    toggle,
    controller,
    listenerBag,
    syncFromContent,
  };
};

registerHydrationOwner({
  id: "view-mode",
  selector: "#view-mode-toggle",
  hydrate: (context) => {
    const root = context instanceof Element ? context : document;
    initViewMode(root);
  },
});
