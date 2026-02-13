import { createPopover } from "../popover.js";
import { createListenerBag } from "../utils/events.js";

const initViewMode = (root = document) => {
  const toggle = root.querySelector?.("#view-mode-toggle");
  const popover = root.querySelector?.("#view-mode-popover");
  const panel = popover?.querySelector?.(".view-mode-panel");
  if (!toggle || !popover || !panel) return;
  if (toggle.dataset.viewReady === "true") return;
  toggle.dataset.viewReady = "true";

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
      const isActive = option.getAttribute("hx-get")?.includes(`view=${view}`);
      option.classList.toggle("is-active", Boolean(isActive));
      if (isActive) {
        option.setAttribute("aria-current", "true");
      } else {
        option.removeAttribute("aria-current");
      }
    });
  };

  const syncFromContent = () => {
    const content =
      root.querySelector?.("#main-content") || document.getElementById("main-content");
    const view = content?.dataset?.view || "diary";
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

  listenerBag.add(document, "htmx:afterSwap", (event) => {
    const target = event.detail?.target;
    if (target && target.id === "main-content") {
      syncFromContent();
      controller.hide();
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
};

initViewMode(document);
document.addEventListener("app:rehydrate", (event) => {
  initViewMode(event?.detail?.context || document);
});
