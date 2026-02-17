import { createPopover } from "../popover.js";
import { getViewState, hydrateViewState } from "../services/view-state.js";
import { createListenerBag } from "../utils/events.js";

let currentInstance = null;

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

initViewMode(document);
document.addEventListener("app:rehydrate", (event) => {
  initViewMode(event?.detail?.context || document);
});
