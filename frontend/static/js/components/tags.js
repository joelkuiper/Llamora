import { createPopover } from "../popover.js";
import { InlineAutocompleteController } from "../utils/inline-autocomplete.js";
import { AutocompleteDataStore } from "../utils/autocomplete-data-store.js";
import { ReactiveElement } from "../utils/reactive-element.js";

const canonicalizeTag = (value, limit = null) => {
  const text = `${value ?? ""}`.replace(/^#/, "").trim();
  if (!text) return "";
  if (Number.isFinite(limit) && limit > 0) {
    return text.slice(0, limit).trim();
  }
  return text;
};

const displayTag = (canonical) => `${canonical ?? ""}`.trim();

const prepareTagAutocompleteValue = (value) =>
  `${value ?? ""}`.replace(/^#/, "").trim();

export const mergeTagCandidateValues = (
  remoteCandidates = [],
  localCandidates = [],
  limit = null
) => {
  const merged = [];
  const seen = new Set();

  const add = (value) => {
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

export class Tags extends ReactiveElement {
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
  #autocompleteStore = null;
  
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
    this.#autocompleteStore = new AutocompleteDataStore({
      debounceMs: 200,
      fetchCandidates: (query, context = {}) =>
        this.#fetchTagAutocompleteCandidates(query, context),
      buildCacheKey: (query, context = {}) =>
        this.#buildAutocompleteCacheKey(query, context),
      getCandidateKey: (candidate) => this.#normalizeTagCandidate(candidate),
      mergeCandidates: (remote, localSets) => this.#mergeAutocompleteCandidates(remote, localSets),
      onError: (error) => {
        if (typeof console !== "undefined" && typeof console.debug === "function") {
          console.debug("Failed to fetch tag autocomplete suggestions", error);
        }
      },
    });
    this.#autocompleteStore.subscribe(
      (candidates) => {
        this.#applyAutocompleteCandidates(candidates);
      },
      { immediate: false }
    );
  }

  connectedCallback() {
    super.connectedCallback();
    this.#cacheElements();
    this.#listeners = this.resetListenerBag(this.#listeners);
    const listeners = this.#listeners;
    this.#destroyAutocomplete();

    if (!this.#button || !this.#popoverEl || !this.#form || !this.#input || !this.#submit || !this.#tagContainer) {
      return;
    }

    listeners.add(this.#input, "input", this.#inputHandler);
    listeners.add(this.#input, "focus", this.#inputFocusHandler);
    listeners.add(
      this.#form,
      "htmx:configRequest",
      this.#configRequestHandler,
    );
    listeners.add(this.#form, "htmx:afterRequest", this.#afterRequestHandler);
    listeners.add(this, "htmx:afterSwap", this.#afterSwapHandler);
    listeners.add(this.#button, "click", this.#buttonClickHandler);
    listeners.add(this.#closeButton, "click", this.#closeClickHandler);
    listeners.add(
      this.#suggestions,
      "htmx:afterSwap",
      this.#suggestionsSwapHandler,
    );
    listeners.add(this.#tagContainer, "click", this.#chipActivationHandler);
    listeners.add(this.#tagContainer, "keydown", this.#chipKeydownHandler);

    this.#initPopover();
    this.#updateSubmitState();
    this.#initAutocomplete();
  }

  disconnectedCallback() {
    this.#destroyPopover();
    this.#teardownListeners();
    this.#destroyAutocomplete();
    super.disconnectedCallback();
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

    this.#button?.setAttribute("aria-expanded", "false");
  }

  #teardownListeners() {
    this.#listeners = this.disposeListenerBag(this.#listeners);
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
        this.#button?.setAttribute("aria-expanded", "true");
        this.classList.add("popover-open");
        if (this.#suggestions && !this.#suggestions.dataset.loaded) {
          htmx.trigger(this.#suggestions, "tag-popover:show");
        }
        if (this.#input && typeof this.#input.focus === "function") {
          try {
            this.#input.focus({ preventScroll: true });
          } catch (error) {
            this.#input.focus();
          }
        }
        this.#scheduleAutocompleteFetch({ immediate: true });
        this.#updateAutocompleteCandidates();
      },
      onHide: () => {
        this.#button.classList.remove("active");
        this.#button?.setAttribute("aria-expanded", "false");
      },
      onHidden: () => {
        this.#button?.setAttribute("aria-expanded", "false");
        this.classList.remove("popover-open");
        if (this.#suggestions) {
          this.#suggestions.innerHTML = "";
          delete this.#suggestions.dataset.loaded;
        }
        this.#cancelPendingAutocompleteFetch();
        this.#autocompleteStore?.reset({ clearLocal: true });
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
    this.#cancelPendingAutocompleteFetch();
  }

  #updateAutocompleteCandidates() {
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
    this.#autocompleteStore?.setLocalEntries("dom", domValues);
    this.#applyAutocompleteCandidates();
  }

  #scheduleAutocompleteFetch({ immediate = false } = {}) {
    if (!this.#input) return;
    const url = this.#getAutocompleteUrl();
    const msgId = this.dataset?.msgId ?? "";
    if (!url || !msgId) {
      this.#autocompleteStore?.cancel();
      return;
    }

    let query = prepareTagAutocompleteValue(this.#input.value ?? "");
    const maxLength = this.#getInputMaxLength();
    if (maxLength) {
      query = query.slice(0, maxLength);
    }

    this.#autocompleteStore?.scheduleFetch(query, { msgId, url }, { immediate });
  }

  #cancelPendingAutocompleteFetch() {
    this.#autocompleteStore?.cancel();
  }

  #clearAutocompleteCache() {
    this.#autocompleteStore?.clearCache();
  }

  #invalidateAutocompleteCache({ immediate = false } = {}) {
    this.#clearAutocompleteCache();
    this.#scheduleAutocompleteFetch({ immediate });
  }

  #applyAutocompleteCandidates(candidates = null) {
    if (!this.#autocomplete) return;
    const list = Array.isArray(candidates)
      ? candidates
      : this.#autocompleteStore?.getCandidates() ?? [];
    if (!list.length) {
      this.#autocomplete.clearCandidates();
      return;
    }
    const limit = this.#getCanonicalMaxLength();
    const entries = list
      .map((item) => {
        const raw = typeof item === "string" ? item : item?.value ?? "";
        const canonical = canonicalizeTag(raw, limit);
        if (!canonical) {
          return null;
        }
        const value = canonical;
        const display = displayTag(canonical);
        return {
          value,
          display,
          tokens: [value],
        };
      })
      .filter(Boolean);

    if (!entries.length) {
      this.#autocomplete.clearCandidates();
      return;
    }

    this.#autocomplete.setCandidates(entries);
  }

  async #fetchTagAutocompleteCandidates(query, context = {}) {
    const url = this.#getAutocompleteUrl();
    const msgId = this.dataset?.msgId ?? context.msgId ?? "";
    if (!url || !msgId) {
      return [];
    }

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

    try {
      const response = await fetch(`${url}?${params.toString()}`, {
        signal: context.signal,
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return [];
      }
      const body = await response.json().catch(() => null);
      if (!body) {
        return [];
      }
      const items = Array.isArray(body?.results) ? body.results : [];
      const values = [];
      const limitLength = this.#getCanonicalMaxLength();
      for (const item of items) {
        let source = null;
        if (typeof item === "string") {
          source = item;
        } else if (item && typeof item.name === "string") {
          source = item.name;
        }
        if (!source) continue;
        const canonical = canonicalizeTag(source, limitLength);
        if (!canonical) continue;
        values.push(canonical);
      }
      return values;
    } catch {
      return [];
    }
  }

  #buildAutocompleteCacheKey(query, context = {}) {
    const limit = this.#getCanonicalMaxLength();
    const canonical = canonicalizeTag(query ?? "", limit);
    const normalized = canonical
      ? canonical.toLowerCase()
      : (query ?? "").trim().toLowerCase();
    const msgId = context.msgId ?? this.dataset?.msgId ?? "";
    return `${msgId}::${normalized}`;
  }

  #normalizeTagCandidate(candidate) {
    const limit = this.#getCanonicalMaxLength();
    if (typeof candidate === "string") {
      const canonical = canonicalizeTag(candidate, limit);
      return canonical ? canonical.toLowerCase() : "";
    }
    if (candidate && typeof candidate.value === "string") {
      const canonical = canonicalizeTag(candidate.value, limit);
      return canonical ? canonical.toLowerCase() : "";
    }
    return "";
  }

  #mergeAutocompleteCandidates(remote, localSets) {
    const locals = [];
    for (const list of localSets) {
      if (!Array.isArray(list) || !list.length) {
        continue;
      }
      locals.push(...list);
    }
    const limit = this.#getCanonicalMaxLength();
    return mergeTagCandidateValues(remote ?? [], locals, limit);
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
    if (!inputLimit || inputLimit <= 0) {
      return null;
    }
    return inputLimit;
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
