import { createPopover } from "../popover.js";

const normalizeTag = (value) => {
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.startsWith("#") ? trimmed : `#${trimmed}`;
};

export class Tags extends HTMLElement {
  #popover = null;
  #button = null;
  #popoverEl = null;
  #form = null;
  #input = null;
  #submit = null;
  #panel = null;
  #suggestions = null;
  #closeButton = null;
  #tagContainer = null;
  #listeners = null;
  #buttonClickHandler;
  #closeClickHandler;
  #inputHandler;
  #configRequestHandler;
  #afterRequestHandler;
  #afterSwapHandler;
  #suggestionsSwapHandler;
  #chipActivationHandler;
  #chipKeydownHandler;

  constructor() {
    super();
    this.#buttonClickHandler = () => this.#togglePopover();
    this.#closeClickHandler = (event) => this.#handleCloseClick(event);
    this.#inputHandler = () => this.#updateSubmitState();
    this.#configRequestHandler = (event) => this.#handleConfigRequest(event);
    this.#afterRequestHandler = () => this.#handleAfterRequest();
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#suggestionsSwapHandler = () => this.#handleSuggestionsSwap();
    this.#chipActivationHandler = (event) => this.#handleChipActivation(event);
    this.#chipKeydownHandler = (event) => this.#handleChipKeydown(event);
  }

  connectedCallback() {
    this.#cacheElements();
    this.#teardownListeners();

    if (!this.#button || !this.#popoverEl || !this.#form || !this.#input || !this.#submit || !this.#tagContainer) {
      return;
    }

    this.#listeners = new AbortController();
    const { signal } = this.#listeners;

    this.#input.addEventListener("input", this.#inputHandler, { signal });
    this.#form.addEventListener("htmx:configRequest", this.#configRequestHandler, {
      signal,
    });
    this.#form.addEventListener("htmx:afterRequest", this.#afterRequestHandler, {
      signal,
    });
    this.addEventListener("htmx:afterSwap", this.#afterSwapHandler, { signal });
    this.#button.addEventListener("click", this.#buttonClickHandler, { signal });
    this.#closeButton?.addEventListener("click", this.#closeClickHandler, {
      signal,
    });
    this.#suggestions?.addEventListener("htmx:afterSwap", this.#suggestionsSwapHandler, {
      signal,
    });
    this.#tagContainer.addEventListener("click", this.#chipActivationHandler, {
      signal,
    });
    this.#tagContainer.addEventListener("keydown", this.#chipKeydownHandler, {
      signal,
    });

    this.#initPopover();
    this.#updateSubmitState();
  }

  disconnectedCallback() {
    this.#destroyPopover();
    this.#teardownListeners();
  }

  #cacheElements() {
    this.#button = this.querySelector(".add-tag-btn");
    this.#popoverEl = this.querySelector(".tag-popover");
    this.#form = this.#popoverEl?.querySelector("form") ?? null;
    this.#input = this.#form?.querySelector('input[name="tag"]') ?? null;
    this.#submit = this.#form?.querySelector('button[type="submit"]') ?? null;
    this.#panel = this.#popoverEl?.querySelector(".tp-content") ?? null;
    this.#suggestions = this.#popoverEl?.querySelector(".tag-suggestions") ?? null;
    this.#closeButton = this.#popoverEl?.querySelector(".overlay-close") ?? null;
    this.#tagContainer = this.querySelector(".meta-tags");
  }

  #teardownListeners() {
    if (this.#listeners) {
      this.#listeners.abort();
      this.#listeners = null;
    }
  }

  #initPopover() {
    this.#destroyPopover();
    if (!this.#button || !this.#popoverEl) {
      return;
    }

    this.#popover = createPopover(this.#button, this.#popoverEl, {
      getPanel: () => this.#panel,
      onShow: () => {
        this.#button.classList.add("active");
        if (this.#suggestions && !this.#suggestions.dataset.loaded) {
          htmx.trigger(this.#suggestions, "tag-popover:show");
        }
        this.#input?.focus();
      },
      onHide: () => {
        this.#button.classList.remove("active");
      },
      onHidden: () => {
        if (this.#suggestions) {
          this.#suggestions.innerHTML = "";
          delete this.#suggestions.dataset.loaded;
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
  }

  #togglePopover() {
    if (!this.#popover) return;
    if (this.#popover.isOpen) {
      this.#popover.hide();
    } else {
      this.#popover.show();
    }
  }

  #handleCloseClick(event) {
    event.preventDefault();
    this.#popover?.hide();
  }

  #getExistingTags() {
    if (!this.#tagContainer) return [];
    return Array.from(this.#tagContainer.querySelectorAll(".chip-label"))
      .map((el) => el.textContent?.trim().toLowerCase())
      .filter(Boolean);
  }

  #updateSubmitState() {
    if (!this.#input || !this.#submit) return;
    const raw = this.#input.value.trim();
    if (!raw) {
      this.#submit.disabled = true;
      return;
    }
    const normalized = normalizeTag(raw).toLowerCase();
    const existing = this.#getExistingTags();
    this.#submit.disabled = existing.includes(normalized);
  }

  #handleConfigRequest(event) {
    if (!this.#input) return;
    const raw = this.#input.value.trim();
    if (!raw) {
      event.preventDefault();
      return;
    }
    const normalized = normalizeTag(raw);
    if (!normalized) {
      event.preventDefault();
      return;
    }
    const existing = this.#getExistingTags();
    if (existing.includes(normalized.toLowerCase())) {
      event.preventDefault();
      return;
    }
    this.#input.value = normalized;
    event.detail.parameters.tag = normalized;
  }

  #handleAfterRequest() {
    this.#form?.reset();
    this.#updateSubmitState();
    this.#popover?.hide();
  }

  #handleAfterSwap(event) {
    const target = event.target;
    if (!target) return;

    if (target === this.#tagContainer) {
      const chip = this.#tagContainer?.lastElementChild;
      if (chip?.classList?.contains("meta-chip")) {
        chip.classList.add("chip-enter");
        chip.addEventListener(
          "animationend",
          () => chip.classList.remove("chip-enter"),
          { once: true }
        );
        const label = chip.querySelector(".chip-label")?.textContent?.trim().toLowerCase();
        if (label && this.#suggestions) {
          this.#suggestions.querySelectorAll(".tag-suggestion").forEach((btn) => {
            if (btn.textContent?.trim().toLowerCase() === label) {
              btn.remove();
            }
          });
        }
      }
      this.#updateSubmitState();
    } else if (target.classList?.contains("chip-tombstone")) {
      target.remove();
      this.#updateSubmitState();
    }
  }

  #handleSuggestionsSwap() {
    if (!this.#suggestions) return;
    if (this.#suggestions.innerHTML.trim()) {
      this.#suggestions.dataset.loaded = "1";
    } else {
      delete this.#suggestions.dataset.loaded;
    }
    this.#popover?.update();
  }

  #handleChipActivation(event) {
    const label = event.target.closest?.(".chip-label");
    if (!label || !(label instanceof HTMLElement)) return;

    const searchInput = document.getElementById("search-input");
    if (!searchInput) return;

    const value = label.textContent?.trim();
    if (!value) return;

    searchInput.value = value;
    if (typeof searchInput.focus === "function") {
      try {
        searchInput.focus({ preventScroll: true });
      } catch (error) {
        searchInput.focus();
      }
    }
  }

  #handleChipKeydown(event) {
    if (![" ", "Enter"].includes(event.key)) return;
    const label = event.target.closest?.(".chip-label");
    if (!label) return;

    event.preventDefault();
    label.click();
  }
}

if (!customElements.get("meta-chips")) {
  customElements.define("meta-chips", Tags);
}
