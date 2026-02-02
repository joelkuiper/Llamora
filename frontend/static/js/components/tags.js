import { createPopover } from "../popover.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import { AutocompleteOverlayMixin } from "./base/autocomplete-overlay.js";
import { AutocompleteHistory } from "../utils/autocomplete-history.js";
import { parsePositiveInteger } from "../utils/number.js";
import { animateMotion } from "../services/motion.js";

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

const TAG_HISTORY_MAX = 50;

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

export class Tags extends AutocompleteOverlayMixin(ReactiveElement) {
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
  #inputListeners = null;
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
  #tagHistory;
  
  constructor() {
    super();
    this.#buttonClickHandler = () => this.#togglePopover();
    this.#closeClickHandler = (event) => this.#handleCloseClick(event);
    this.#inputHandler = () => {
      this.#updateSubmitState();
      this.scheduleAutocompleteFetch();
    };
    this.#inputFocusHandler = () => this.#handleInputFocus();
    this.#configRequestHandler = (event) => this.#handleConfigRequest(event);
    this.#afterRequestHandler = () => this.#handleAfterRequest();
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#suggestionsSwapHandler = () => this.#handleSuggestionsSwap();
    this.#chipActivationHandler = (event) => this.#handleChipActivation(event);
    this.#chipKeydownHandler = (event) => this.#handleChipKeydown(event);
    this.#tagHistory = new AutocompleteHistory({
      maxEntries: TAG_HISTORY_MAX,
      prepare: (value) => {
        const limit = this.#getCanonicalMaxLength();
        if (typeof value === "string") {
          return canonicalizeTag(value, limit) || null;
        }
        if (value && typeof value.value === "string") {
          return canonicalizeTag(value.value, limit) || null;
        }
        return null;
      },
      normalize: (value) => {
        if (typeof value === "string") {
          return value.trim().toLowerCase();
        }
        if (value && typeof value.value === "string") {
          const limit = this.#getCanonicalMaxLength();
          const canonical = canonicalizeTag(value.value, limit);
          return canonical ? canonical.toLowerCase() : "";
        }
        return "";
      },
    });
  }

  connectedCallback() {
    super.connectedCallback();
    this.#cacheElements();
    this.#listeners = this.resetListenerBag(this.#listeners);
    const listeners = this.#listeners;

    if (!this.#button || !this.#popoverEl || !this.#form || !this.#input || !this.#submit || !this.#tagContainer) {
      return;
    }

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
  }

  disconnectedCallback() {
    this.#destroyPopover();
    this.#teardownListeners();
    this.#inputListeners = this.disposeListenerBag(this.#inputListeners);
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
        if (this.#suggestions && this.dataset?.suggestionsUrl) {
          this.#suggestions.setAttribute("hx-get", this.dataset.suggestionsUrl);
        }
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
        this.scheduleAutocompleteFetch({ immediate: true });
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
        this.cancelAutocompleteFetch();
        this.resetAutocompleteStore({ clearLocal: true });
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

  #shouldFetchAutocomplete() {
    if (!this.#input) {
      return false;
    }
    if (this.#popover?.isOpen) {
      return true;
    }
    if (
      typeof document !== "undefined" &&
      document.activeElement === this.#input
    ) {
      return true;
    }
    return false;
  }

  #handleInputFocus() {
    this.scheduleAutocompleteFetch({ immediate: true });
    this.#updateAutocompleteCandidates();
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
        animateMotion(chip, "motion-animate-chip-enter");
        const limit = this.#getCanonicalMaxLength();
        const label = chip
          .querySelector(".chip-label")
          ?.textContent?.trim();
        const canonicalValue = canonicalizeTag(label ?? "", limit);
        const canonicalKey = canonicalValue?.toLowerCase();
        if (canonicalKey && this.#suggestions) {
          this.#suggestions.querySelectorAll(".tag-suggestion").forEach((btn) => {
            const btnCanonical = canonicalizeTag(
              btn.dataset.tag ?? btn.textContent ?? "",
              limit
            )?.toLowerCase();
            if (btnCanonical === canonicalKey) {
              btn.remove();
            }
          });
        }
        if (canonicalValue) {
          this.#tagHistory.add(canonicalValue);
          this.setAutocompleteLocalEntries(
            "history",
            this.#tagHistory.values()
          );
          this.applyAutocompleteCandidates();
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

  getAutocompleteControllerOptions() {
    return {
      prepareQuery: prepareTagAutocompleteValue,
      prepareCandidate: prepareTagAutocompleteValue,
    };
  }

  getAutocompleteInputConfig() {
    return {
      selector: 'form input[name="tag"]',
      observe: true,
      root: () => this.querySelector(".tag-popover") ?? this,
    };
  }

  getAutocompleteStoreOptions() {
    return {
      debounceMs: 200,
      fetchCandidates: (query, context = {}) =>
        this.#fetchTagAutocompleteCandidates(query, context),
      buildCacheKey: (query, context = {}) =>
        this.#buildAutocompleteCacheKey(query, context),
      getCandidateKey: (candidate) => this.#normalizeTagCandidate(candidate),
      mergeCandidates: (remote, localSets, helpers) =>
        this.#mergeAutocompleteCandidates(remote, localSets, helpers),
      onError: (error) => {
        if (typeof console !== "undefined" && typeof console.debug === "function") {
          console.debug("Failed to fetch tag autocomplete suggestions", error);
        }
      },
    };
  }

  transformAutocompleteCandidates(candidates) {
    const list = Array.isArray(candidates) ? candidates : [];
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
        return { value, display, tokens: [value] };
      })
      .filter(Boolean);
    return entries;
  }

  buildAutocompleteFetchParams() {
    const input = this.autocompleteInput;
    const url = this.#getSuggestionsUrl();
    if (!input || !url) {
      return null;
    }
    let query = prepareTagAutocompleteValue(input.value ?? "");
    const maxLength = this.#getInputMaxLength();
    if (maxLength) {
      query = query.slice(0, maxLength);
    }
    return { query, context: { url } };
  }

  onAutocompleteCommit() {
    this.#updateSubmitState();
  }

  normalizeAutocompleteCandidate(candidate) {
    return this.#normalizeTagCandidate(candidate);
  }

  onAutocompleteInputChanged(input, previous, meta = {}) {
    const next = input instanceof HTMLInputElement ? input : null;
    this.#input = next;

    this.#inputListeners = this.disposeListenerBag(this.#inputListeners);

    if (next) {
      const bag = this.createListenerBag();
      bag.add(next, "input", this.#inputHandler);
      bag.add(next, "focus", this.#inputFocusHandler);
      this.#inputListeners = bag;
    }

    this.#initAutocomplete();
    this.#updateSubmitState();

    if (meta?.initialized && this.#shouldFetchAutocomplete()) {
      this.scheduleAutocompleteFetch({ immediate: true });
    }
  }

  #initAutocomplete() {
    const input = this.#input ?? this.autocompleteInput;
    if (!(input instanceof HTMLInputElement)) {
      return;
    }

    this.#input = input;
    input.setAttribute("autocomplete", "off");
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("autocorrect", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("data-lpignore", "true");
    input.setAttribute("data-1p-ignore", "true");
    this.#updateAutocompleteCandidates();
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
    this.setAutocompleteLocalEntries("dom", domValues);
    this.setAutocompleteLocalEntries("history", this.#tagHistory.values());
    this.applyAutocompleteCandidates();
  }

  #invalidateAutocompleteCache({ immediate = false } = {}) {
    this.clearAutocompleteCache();
    if (this.#shouldFetchAutocomplete()) {
      this.scheduleAutocompleteFetch({ immediate });
    }
  }

  async #fetchTagAutocompleteCandidates(query, context = {}) {
    const url = context.url ?? this.#getSuggestionsUrl();
    if (!url) {
      return [];
    }

    const params = new URLSearchParams();
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
    const url = context.url ?? this.#getSuggestionsUrl() ?? "";
    return `${url}::${normalized}`;
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

  #mergeAutocompleteCandidates(remote, localSets, _helpers = {}) {
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

  #getSuggestionsUrl() {
    return this.dataset?.suggestionsUrl ?? "";
  }

  #getAutocompleteLimit() {
    const raw = this.dataset?.autocompleteLimit ?? "";
    return parsePositiveInteger(raw, null);
  }

  #getInputMaxLength() {
    const attr = this.#input?.getAttribute("maxlength") ?? "";
    return parsePositiveInteger(attr, null);
  }

  #getCanonicalMaxLength() {
    const inputLimit = this.#getInputMaxLength();
    if (!inputLimit || inputLimit <= 0) {
      return null;
    }
    return inputLimit;
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
