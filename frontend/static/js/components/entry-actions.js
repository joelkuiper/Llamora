import { createPopover } from "../popover.js";
import { createListenerBag } from "../utils/events.js";
import { ReactiveElement } from "../utils/reactive-element.js";

let sharedActionPopoverEl = null;
let activeActionOwner = null;

const getSharedActionPopoverEl = () => {
  if (!sharedActionPopoverEl || !sharedActionPopoverEl.isConnected) {
    sharedActionPopoverEl = document.getElementById("action-popover-global");
  }
  return sharedActionPopoverEl;
};

class EntryActions extends ReactiveElement {
  #button = null;
  #popoverEl = null;
  #panel = null;
  #closeButton = null;
  #popover = null;
  #listeners = null;
  #sharedListeners = null;
  #actionItems = [];

  connectedCallback() {
    super.connectedCallback();
    this.#cacheElements();
    if (!this.#button) {
      return;
    }
    this.#listeners = this.resetListenerBag(this.#listeners);
    this.#listeners.add(this.#button, "click", (event) => {
      if (this.#isResponseActive()) {
        return;
      }
      if (this.#button?.hasAttribute("disabled") || this.#button?.getAttribute("aria-disabled") === "true") {
        return;
      }
      event.preventDefault();
      this.#togglePopover();
    });
  }

  disconnectedCallback() {
    if (activeActionOwner === this) {
      this.#deactivateSharedOwner();
    } else {
      this.#destroyPopover();
      this.#detachSharedListeners();
    }
    this.#listeners = this.disposeListenerBag(this.#listeners);
    super.disconnectedCallback();
  }

  #cacheElements() {
    this.#button = this.querySelector(".action-trigger");
    this.#button?.setAttribute("aria-expanded", "false");
  }

  #cacheSharedElements() {
    this.#popoverEl = getSharedActionPopoverEl();
    this.#panel = this.#popoverEl?.querySelector(".action-panel") ?? null;
    this.#closeButton = this.#popoverEl?.querySelector(".overlay-close") ?? null;
    this.#actionItems = this.#popoverEl
      ? Array.from(this.#popoverEl.querySelectorAll(".action-item"))
      : [];
  }

  #activateSharedOwner() {
    if (activeActionOwner && activeActionOwner !== this) {
      activeActionOwner.#deactivateSharedOwner();
    }
    activeActionOwner = this;
    this.#attachSharedListeners();
  }

  #deactivateSharedOwner() {
    this.#popover?.hide();
    this.#destroyPopover();
    this.#detachSharedListeners();
    if (activeActionOwner === this) {
      activeActionOwner = null;
    }
  }

  #attachSharedListeners() {
    if (!this.#popoverEl) {
      return;
    }
    this.#sharedListeners = this.resetListenerBag(this.#sharedListeners);
    const listeners = this.#sharedListeners;
    if (this.#closeButton) {
      listeners.add(this.#closeButton, "click", (event) => {
        event.preventDefault();
        this.#popover?.hide();
      });
    }
    listeners.add(this.#popoverEl, "click", (event) => this.#handleActionClick(event));
  }

  #detachSharedListeners() {
    this.#sharedListeners = this.disposeListenerBag(this.#sharedListeners);
  }

  #prepareActionItems() {
    if (!this.#actionItems.length) {
      return;
    }
    const entryId = this.dataset.entryId ?? "";
    const day =
      document.getElementById("entries")?.dataset?.date ??
      document.body?.dataset?.activeDay ??
      "";
    if (!entryId || !day) {
      return;
    }
    const postUrl = `/e/${day}/response/${entryId}`;
    const target = `#entry-responses-${entryId}`;
    this.#actionItems.forEach((action) => {
      const kind = action.dataset?.actionKind ?? "";
      action.setAttribute("hx-post", postUrl);
      action.setAttribute("hx-target", target);
      action.setAttribute("hx-swap", "beforeend");
      action.setAttribute("hx-include", "#user-time");
      if (kind) {
        action.setAttribute("hx-vals", JSON.stringify({ response_kind: kind }));
      }
    });
    if (typeof htmx !== "undefined") {
      htmx.process(this.#popoverEl);
    }
  }

  #initPopover() {
    this.#destroyPopover();
    this.#cacheSharedElements();
    if (!this.#button || !this.#popoverEl) {
      return;
    }

    this.#popover = createPopover(this.#button, this.#popoverEl, {
      placement: "bottom",
      getPanel: () => this.#panel,
      onBeforeShow: () => {
        this.#activateSharedOwner();
        this.#prepareActionItems();
      },
      onShow: () => {
        this.#button?.classList.add("active");
        this.#button?.setAttribute("aria-expanded", "true");
        this.classList.add("popover-open");
        const firstAction = this.#actionItems[0];
        if (firstAction && typeof firstAction.focus === "function") {
          try {
            firstAction.focus({ preventScroll: true });
          } catch (error) {
            firstAction.focus();
          }
        }
      },
      onHide: () => {
        this.#button?.classList.remove("active");
        this.#button?.setAttribute("aria-expanded", "false");
      },
      onHidden: () => {
        this.classList.remove("popover-open");
        if (activeActionOwner === this && !this.#popover?.isOpen) {
          this.#detachSharedListeners();
          activeActionOwner = null;
        }
      },
    });
  }

  #destroyPopover() {
    if (this.#popover) {
      this.#popover.destroy();
      this.#popover = null;
    }
    this.#button?.classList.remove("active");
    this.classList.remove("popover-open");
  }

  #togglePopover() {
    if (this.#popover?.isOpen) {
      this.#popover.hide();
      return;
    }
    this.#destroyPopover();
    this.#initPopover();
    if (!this.#popover) return;
    this.#popover.show();
  }

  #handleActionClick(event) {
    const target = event.target;
    const action = target?.closest?.(".action-item");
    if (!action) {
      return;
    }
    // Hide immediately so it can't linger in a default position after HTMX request.
    if (this.#popover) {
      this.#popover.hide();
      this.#popoverEl.hidden = true;
    }
  }

  #isResponseActive() {
    return this.dataset.responseActive === "true";
  }
}

if (typeof customElements !== "undefined" && !customElements.get("entry-actions")) {
  customElements.define("entry-actions", EntryActions);
}
