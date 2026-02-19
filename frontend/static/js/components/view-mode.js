import { createPopover } from "../popover.js";
import { normalizeIsoDay } from "../services/day-resolution.js";
import { getFrameState, hydrateFrame } from "../services/app-state.js";
import { registerHydrationOwner } from "../services/hydration-owners.js";
import { createListenerBag } from "../utils/events.js";
import { sessionStore } from "../utils/storage.js";
import { getActiveDay } from "./entries-view/active-day-store.js";
import { buildTagPageUrl, parseTagFromPath } from "./tags-view/tags-nav-url.js";

let currentInstance = null;
const TAGS_CONTEXT_KEY = "tags:last-context";

const readStoredTagsContext = () => {
  const raw = sessionStore.get(TAGS_CONTEXT_KEY);
  if (!raw || typeof raw !== "object") return null;
  return {
    selectedTag: String(raw.selectedTag || "").trim(),
    day: normalizeIsoDay(raw.day),
  };
};

const writeStoredTagsContext = (context) => {
  if (!context || typeof context !== "object") return;
  sessionStore.set(TAGS_CONTEXT_KEY, {
    selectedTag: String(context.selectedTag || "").trim(),
    day: normalizeIsoDay(context.day),
  });
};

/**
 * Resolves the navigation day for view-mode switching.
 *
 * 🟢 Frame state is authoritative when in the tags view (day always present in URL).
 * 🔵 active-day-store is the fallback for the diary view: the /e/<date> fragment
 *    endpoint does not embed a view-state JSON, so frame state can lag behind
 *    after in-page calendar navigation. The active-day-store is updated by
 *    entry-view.js after every render and is always current in that case.
 */
const resolveNavigationDay = () => {
  const frame = getFrameState();
  if (frame.view === "tags") return frame.day;
  return getActiveDay() || frame.day;
};

const buildDiaryUrl = (day) => {
  const resolved = normalizeIsoDay(day);
  return resolved ? `/d/${resolved}` : "/d/today";
};

const buildTagsUrl = ({ selectedTag = "", day = "" } = {}) => buildTagPageUrl(selectedTag, { day });

const getLiveTagsContext = () => {
  const frame = getFrameState();
  const stored = readStoredTagsContext();
  const selectedTag =
    (frame.view === "tags" ? frame.selectedTag : "") ||
    parseTagFromPath(window.location.pathname) ||
    String(stored?.selectedTag || "").trim();
  const day = resolveNavigationDay() || normalizeIsoDay(stored?.day);
  const context = { selectedTag, day };
  writeStoredTagsContext(context);
  return context;
};

const persistTagsContextFromState = () => {
  const frame = getFrameState();
  if (frame.view !== "tags") return;
  writeStoredTagsContext({
    selectedTag: frame.selectedTag,
    day: frame.day,
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
    hydrateFrame(document);
    const { view } = getFrameState();
    setActive(view || "diary");
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

  listenerBag.add(document.body, "htmx:configRequest", (event) => {
    const source = event?.detail?.elt;
    if (!(source instanceof Element)) return;
    const option = source.closest(".view-mode-option[data-view-mode]");
    if (!(option instanceof HTMLElement) || !panel.contains(option)) return;

    const mode = String(option.dataset.viewMode || "").trim();
    if (mode === "diary") {
      event.detail.path = buildDiaryUrl(resolveNavigationDay());
      return;
    }
    if (mode === "tags") {
      event.detail.path = buildTagsUrl(getLiveTagsContext());
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
