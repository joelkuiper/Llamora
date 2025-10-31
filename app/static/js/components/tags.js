import { createPopover } from "../popover.js";
import { InlineAutocompleteController } from "../utils/inline-autocomplete.js";

const BaseHTMLElement =
  typeof HTMLElement !== "undefined" ? HTMLElement : class {};

const canonicalizeTag = (value, limit = null) => {
  if (typeof value !== "string") return "";
  let trimmed = value.trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("#")) {
    trimmed = trimmed.slice(1);
  }
  trimmed = trimmed.trim();
  if (!trimmed) return "";
  if (Number.isFinite(limit) && limit > 0) {
    trimmed = trimmed.slice(0, limit);
    trimmed = trimmed.trim();
    if (!trimmed) return "";
  }
  return trimmed;
};

const displayTag = (canonical) => {
  const name = typeof canonical === "string" ? canonical.trim() : "";
  return name ? `#${name}` : "#";
};

const prepareTagAutocompleteValue = (value) => {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  return trimmed;
};

export const mergeTagCandidateValues = (
  remoteCandidates = [],
  localCandidates = [],
  limit = null
) => {
  const merged = [];
  const seen = new Set();

  const add = (value) => {
    if (typeof value !== "string") return;
    const canonical = canonicalizeTag(value, limit);
    if (!canonical) return;
    const key = canonical.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(canonical);
  };

  remoteCandidates.forEach(add);
  localCandidates.forEach(add);

  return merged;
};

export class Tags extends BaseHTMLElement {
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
  #autocomplete = null;
  #buttonClickHandler;
  #closeClickHandler;
  #inputHandler;
  #inputFocusHandler;
  #configRequestHandler;
  #afterRequestHandler;
  #afterSwapHandler;
  #suggestionsSwapHandler;
  #chipActivationHandler;
  #chipKeydownHandler;
  #remoteCandidates = [];
  #autocompleteCache = null;
  #autocompleteFetchTimer = null;
  #autocompleteFetchController = null;
  #lastAutocompleteQueryKey = null;
  #pendingAutocompleteQueryKey = null;
  
  constructor() {
    super();
    this.#buttonClickHandler = () => this.#togglePopover();
    this.#closeClickHandler = (event) => this.#handleCloseClick(event);
    this.#inputHandler = () => {
      this.#updateSubmitState();
      this.#scheduleAutocompleteFetch();
    };
    this.#inputFocusHandler = () => this.#handleInputFocus();
    this.#configRequestHandler = (event) => this.#handleConfigRequest(event);
    this.#afterRequestHandler = () => this.#handleAfterRequest();
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#suggestionsSwapHandler = () => this.#handleSuggestionsSwap();
    this.#chipActivationHandler = (event) => this.#handleChipActivation(event);
    this.#chipKeydownHandler = (event) => this.#handleChipKeydown(event);
    this.#autocompleteCache = new Map();
  }

  connectedCallback() {
    this.#cacheElements();
    this.#teardownListeners();
    this.#destroyAutocomplete();

    if (!this.#button || !this.#popoverEl || !this.#form || !this.#input || !this.#submit || !this.#tagContainer) {
      return;
    }


    this.#listeners = new AbortController();
    const { signal } = this.#listeners;

    this.#input.addEventListener("input", this.#inputHandler, { signal });
    this.#input.addEventListener("focus", this.#inputFocusHandler, { signal });
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
    this.#initAutocomplete();
  }

  disconnectedCallback() {
    this.#destroyPopover();
    this.#teardownListeners();
    this.#destroyAutocomplete();
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
        this.#scheduleAutocompleteFetch({ immediate: true });
        this.#updateAutocompleteCandidates();
      },
      onHide: () => {
        this.#button.classList.remove("active");
      },
      onHidden: () => {
        if (this.#suggestions) {
          this.#suggestions.innerHTML = "";
          delete this.#suggestions.dataset.loaded;
        }
        this.#cancelPendingAutocompleteFetch();
        this.#remoteCandidates = [];
        this.#autocomplete?.clearCandidates();
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
    const seen = new Set();
    if (!this.#tagContainer) return seen;
    const limit = this.#getCanonicalMaxLength();
    this.#tagContainer.querySelectorAll(".chip-label").forEach((el) => {
      const canonical = canonicalizeTag(el.textContent ?? "", limit);
      if (!canonical) return;
      seen.add(canonical.toLowerCase());
    });
    return seen;
  }

  #handleInputFocus() {
    this.#scheduleAutocompleteFetch({ immediate: true });
  }

  #updateSubmitState() {
    if (!this.#input || !this.#submit) return;
    const raw = this.#input.value.trim();
    if (!raw) {
      this.#submit.disabled = true;
      return;
    }
    const limit = this.#getCanonicalMaxLength();
    const canonical = canonicalizeTag(raw, limit);
    if (!canonical) {
      this.#submit.disabled = true;
      return;
    }
    const existing = this.#getExistingTags();
    this.#submit.disabled = existing.has(canonical.toLowerCase());
  }

  #handleConfigRequest(event) {
    if (!this.#input) return;
    const raw = this.#input.value.trim();
    if (!raw) {
      event.preventDefault();
      return;
    }
    const limit = this.#getCanonicalMaxLength();
    const canonical = canonicalizeTag(raw, limit);
    if (!canonical) {
      event.preventDefault();
      return;
    }
    const existing = this.#getExistingTags();
    if (existing.has(canonical.toLowerCase())) {
      event.preventDefault();
      return;
    }
    const display = displayTag(canonical);
    this.#input.value = display;
    event.detail.parameters.tag = canonical;
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
        const limit = this.#getCanonicalMaxLength();
        const label = chip
          .querySelector(".chip-label")
          ?.textContent?.trim();
        const canonical = canonicalizeTag(label ?? "", limit)?.toLowerCase();
        if (canonical && this.#suggestions) {
          this.#suggestions.querySelectorAll(".tag-suggestion").forEach((btn) => {
            const btnCanonical = canonicalizeTag(
              btn.dataset.tag ?? btn.textContent ?? "",
              limit
            )?.toLowerCase();
            if (btnCanonical === canonical) {
              btn.remove();
            }
          });
        }
      }
      this.#updateSubmitState();
      this.#invalidateAutocompleteCache({ immediate: true });
    } else if (target.classList?.contains("chip-tombstone")) {
      target.remove();
      this.#updateSubmitState();
      this.#invalidateAutocompleteCache({ immediate: true });
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
    this.#updateAutocompleteCandidates();
  }

  #initAutocomplete() {
    if (!this.#input) return;
    this.#input.setAttribute("autocomplete", "off");
    this.#input.setAttribute("autocapitalize", "off");
    this.#input.setAttribute("autocorrect", "off");
    this.#input.setAttribute("spellcheck", "false");
    this.#input.setAttribute("data-lpignore", "true");
    this.#input.setAttribute("data-1p-ignore", "true");
    this.#autocomplete = new InlineAutocompleteController(this.#input, {
      prepareQuery: prepareTagAutocompleteValue,
      prepareCandidate: prepareTagAutocompleteValue,
      onCommit: () => {
        this.#updateSubmitState();
      },
    });
    this.#updateAutocompleteCandidates();
  }

  #destroyAutocomplete() {
    if (this.#autocomplete) {
      this.#autocomplete.destroy();
    }
    this.#autocomplete = null;
    this.#cancelPendingAutocompleteFetch({ resetQueryKey: true });
    this.#remoteCandidates = [];
  }

  #updateAutocompleteCandidates() {
    if (!this.#autocomplete) return;
    const domValues = [];
    if (this.#suggestions) {
      this.#suggestions.querySelectorAll(".tag-suggestion").forEach((btn) => {
        const text = btn.textContent?.trim();
        if (!text) {
          return;
        }
        const canonical = canonicalizeTag(
          btn.dataset.tag ?? text,
          this.#getCanonicalMaxLength()
        );
        if (!canonical) return;
        domValues.push(canonical);
      });
    }

    const limit = this.#getCanonicalMaxLength();
    const values = mergeTagCandidateValues(
      this.#remoteCandidates,
      domValues,
      limit
    );
    const entries = values.map((canonical) => {
      const value = displayTag(canonical);
      return {
        value,
        display: value,
        tokens: [value],
      };
    });

    this.#autocomplete.setCandidates(entries);
  }

  #scheduleAutocompleteFetch({ immediate = false } = {}) {
    if (!this.#input) return;
    const url = this.#getAutocompleteUrl();
    const msgId = this.dataset?.msgId ?? "";
    if (!url || !msgId) return;

    let query = prepareTagAutocompleteValue(this.#input.value ?? "");
    const maxLength = this.#getInputMaxLength();
    if (maxLength) {
      query = query.slice(0, maxLength);
    }

    const cacheKey = this.#buildCacheKey(query);

    if (!immediate && this.#pendingAutocompleteQueryKey === cacheKey && this.#autocompleteFetchTimer) {
      return;
    }

    this.#pendingAutocompleteQueryKey = cacheKey;

    if (this.#autocompleteFetchTimer) {
      globalThis.clearTimeout(this.#autocompleteFetchTimer);
      this.#autocompleteFetchTimer = null;
    }

    const delay = immediate ? 0 : 200;
    this.#autocompleteFetchTimer = globalThis.setTimeout(() => {
      this.#autocompleteFetchTimer = null;
      this.#requestAutocomplete(query, cacheKey);
    }, delay);
  }

  #requestAutocomplete(query, cacheKey) {
    const url = this.#getAutocompleteUrl();
    const msgId = this.dataset?.msgId ?? "";
    if (!url || !msgId) {
      this.#remoteCandidates = [];
      this.#updateAutocompleteCandidates();
      return;
    }

    const key = cacheKey ?? this.#buildCacheKey(query);
    this.#lastAutocompleteQueryKey = key;

    if (this.#autocompleteCache?.has(key)) {
      const cached = this.#autocompleteCache.get(key) ?? [];
      this.#remoteCandidates = cached.slice();
      this.#updateAutocompleteCandidates();
      return;
    }

    if (this.#autocompleteFetchController) {
      this.#autocompleteFetchController.abort();
    }

    const controller = new AbortController();
    this.#autocompleteFetchController = controller;

    const params = new URLSearchParams();
    params.set("msg_id", msgId);
    if (query) {
      params.set("q", query);
    }
    const limit = this.#getAutocompleteLimit();
    if (limit) {
      const clamped = Math.min(Math.max(limit, 1), 50);
      params.set("limit", String(clamped));
    }

    fetch(`${url}?${params.toString()}`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`);
        }
        return response.json();
      })
      .then((body) => {
        if (controller.signal.aborted) return;
        const items = Array.isArray(body?.results) ? body.results : [];
        const values = [];
        const limit = this.#getCanonicalMaxLength();
        for (const item of items) {
          let source = null;
          if (typeof item === "string") {
            source = item;
          } else if (item && typeof item.name === "string") {
            source = item.name;
          }
          if (!source) continue;
          const canonical = canonicalizeTag(source, limit);
          if (!canonical) continue;
          values.push(canonical);
        }
        this.#autocompleteCache?.set(key, values);
        if (this.#lastAutocompleteQueryKey === key) {
          this.#remoteCandidates = values;
          this.#updateAutocompleteCandidates();
        }
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return;
        }
        if (typeof console !== "undefined" && typeof console.debug === "function") {
          console.debug("Failed to fetch tag autocomplete suggestions", error);
        }
        if (this.#lastAutocompleteQueryKey === key) {
          this.#remoteCandidates = [];
          this.#updateAutocompleteCandidates();
        }
      })
      .finally(() => {
        if (this.#autocompleteFetchController === controller) {
          this.#autocompleteFetchController = null;
        }
      });
  }

  #cancelPendingAutocompleteFetch({ resetQueryKey = false } = {}) {
    if (this.#autocompleteFetchTimer !== null) {
      globalThis.clearTimeout(this.#autocompleteFetchTimer);
      this.#autocompleteFetchTimer = null;
    }
    if (this.#autocompleteFetchController) {
      this.#autocompleteFetchController.abort();
      this.#autocompleteFetchController = null;
    }
    if (resetQueryKey) {
      this.#pendingAutocompleteQueryKey = null;
      this.#lastAutocompleteQueryKey = null;
    }
  }

  #clearAutocompleteCache() {
    this.#autocompleteCache?.clear();
    this.#pendingAutocompleteQueryKey = null;
    this.#lastAutocompleteQueryKey = null;
  }

  #invalidateAutocompleteCache({ immediate = false } = {}) {
    this.#clearAutocompleteCache();
    this.#remoteCandidates = [];
    this.#updateAutocompleteCandidates();
    this.#scheduleAutocompleteFetch({ immediate });
  }

  #getAutocompleteUrl() {
    return this.dataset?.autocompleteUrl ?? "";
  }

  #getAutocompleteLimit() {
    const raw = this.dataset?.autocompleteLimit ?? "";
    const value = Number.parseInt(raw, 10);
    if (Number.isFinite(value) && value > 0) {
      return value;
    }
    return null;
  }

  #getInputMaxLength() {
    const attr = this.#input?.getAttribute("maxlength") ?? "";
    const value = Number.parseInt(attr, 10);
    if (Number.isFinite(value) && value > 0) {
      return value;
    }
    return null;
  }

  #getCanonicalMaxLength() {
    const inputLimit = this.#getInputMaxLength();
    if (!inputLimit || inputLimit <= 1) {
      return null;
    }
    return inputLimit - 1;
  }

  #buildCacheKey(query) {
    const msgId = this.dataset?.msgId ?? "";
    const limit = this.#getCanonicalMaxLength();
    const canonical = canonicalizeTag(query ?? "", limit);
    const normalized = canonical
      ? canonical.toLowerCase()
      : (query ?? "").trim().toLowerCase();
    return `${msgId}::${normalized}`;
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

if (typeof customElements !== "undefined" && !customElements.get("meta-chips")) {
  customElements.define("meta-chips", Tags);
}
