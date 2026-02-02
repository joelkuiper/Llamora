import { createPopover } from "../popover.js";
import { createListenerBag } from "../utils/events.js";
import { ReactiveElement } from "../utils/reactive-element.js";

class EntryActions extends ReactiveElement {
  #button = null;
  #popoverEl = null;
  #panel = null;
  #closeButton = null;
  #popover = null;
  #listeners = null;

  connectedCallback() {
    super.connectedCallback();
    this.#cacheElements();
    if (!this.#isResponseActive()) {
      this.#initPopover();
    }
    this.#listeners = this.resetListenerBag(this.#listeners);
    this.#listeners.add(this.#button, "click", (event) => {
      if (this.#isResponseActive()) {
        return;
      }
      event.preventDefault();
      this.#togglePopover();
    });
    this.#listeners.add(this.#closeButton, "click", (event) => {
      event.preventDefault();
      this.#popover?.hide();
    });
    this.#listeners.add(this, "click", (event) => this.#handleActionClick(event));
  }

  disconnectedCallback() {
    this.#destroyPopover();
    this.#listeners = this.disposeListenerBag(this.#listeners);
    super.disconnectedCallback();
  }

  #cacheElements() {
    this.#button = this.querySelector(".action-trigger");
    this.#popoverEl = this.querySelector(".action-popover");
    this.#panel = this.#popoverEl?.querySelector(".action-panel") ?? null;
    this.#closeButton = this.#popoverEl?.querySelector(".overlay-close") ?? null;
    this.#button?.setAttribute("aria-expanded", "false");
  }

  #initPopover() {
    this.#destroyPopover();
    if (!this.#button || !this.#popoverEl) {
      return;
    }

    this.#popover = createPopover(this.#button, this.#popoverEl, {
      placement: "bottom",
      getPanel: () => this.#panel,
      onShow: () => {
        this.#button?.classList.add("active");
        this.#button?.setAttribute("aria-expanded", "true");
        this.classList.add("popover-open");
        const firstAction = this.querySelector(".action-item");
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
    if (!this.#popover) return;
    if (this.#popover.isOpen) {
      this.#popover.hide();
    } else {
      this.#popover.show();
    }
  }

  #handleActionClick(event) {
    const target = event.target;
    const action = target?.closest?.(".action-item");
    if (!action) {
      return;
    }
    this.#popover?.hide();
  }

  #isResponseActive() {
    return this.dataset.responseActive === "true";
  }
}

if (typeof customElements !== "undefined" && !customElements.get("entry-actions")) {
  customElements.define("entry-actions", EntryActions);
}
