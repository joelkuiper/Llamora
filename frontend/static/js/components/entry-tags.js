import { createPopover } from "../popover.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import { AutocompleteOverlayMixin } from "./base/autocomplete-overlay.js";
import { AutocompleteHistory } from "../utils/autocomplete-history.js";
import { parsePositiveInteger } from "../utils/number.js";
import { animateMotion } from "../services/motion.js";
import { scrollToHighlight } from "../ui.js";
import { formatTimeElements } from "../services/time.js";

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
const TAG_SUMMARY_CACHE_PREFIX = "llamora:tag-summary:";
const TAG_SUMMARY_CACHE_TTL = 1000 * 60 * 60 * 6;
const debugTagDetail = (...args) => {
  // HACK: always-on debug tracing for tag popover behavior.
  console.debug("[tag-detail]", ...args);
};

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

export class EntryTags extends AutocompleteOverlayMixin(ReactiveElement) {
  #popover = null;
  #button = null;
  #popoverEl = null;
  #form = null;
  #input = null;
  #submit = null;
  #panel = null;
  #suggestions = null;
  #closeButton = null;
  #detailPopover = null;
  #detailPopoverEl = null;
  #detailPanel = null;
  #detailCloseButton = null;
  #detailBody = null;
  #detailSkeleton = "";
  #activeTagEl = null;
  #activeTagLabel = null;
  #activeTagHash = "";
  #detailOutsideListeners = null;
  #tagContainer = null;
  #listeners = null;
  #inputListeners = null;
  #buttonClickHandler;
  #closeClickHandler;
  #detailCloseClickHandler;
  #detailClickHandler;
  #detailAfterSwapHandler;
  #pageShowHandler;
  #pageHideHandler;
  #beforeHistorySaveHandler;
  #restoredHandler;
  #visibilityHandler;
  #inputHandler;
  #inputFocusHandler;
  #configRequestHandler;
  #afterRequestHandler;
  #afterSwapHandler;
  #suggestionsSwapHandler;
  #tagActivationHandler;
  #tagKeydownHandler;
  #tagHistory;
  
  constructor() {
    super();
    this.#buttonClickHandler = () => this.#togglePopover();
    this.#closeClickHandler = (event) => this.#handleCloseClick(event);
    this.#detailCloseClickHandler = (event) => this.#handleDetailCloseClick(event);
    this.#detailClickHandler = (event) => this.#handleDetailClick(event);
    this.#detailAfterSwapHandler = (event) => this.#handleDetailAfterSwap(event);
    this.#pageShowHandler = () => this.#resetDetailPopoverState();
    this.#pageHideHandler = () => this.#forceHideDetailPopover("pagehide");
    this.#beforeHistorySaveHandler = () =>
      this.#forceHideDetailPopover("htmx:beforeHistorySave");
    this.#restoredHandler = () => this.#resetDetailPopoverState("htmx:restored");
    this.#visibilityHandler = () => this.#handleVisibility();
    this.#inputHandler = () => {
      this.#updateSubmitState();
      this.scheduleAutocompleteFetch();
    };
    this.#inputFocusHandler = () => this.#handleInputFocus();
    this.#configRequestHandler = (event) => this.#handleConfigRequest(event);
    this.#afterRequestHandler = () => this.#handleAfterRequest();
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#suggestionsSwapHandler = () => this.#handleSuggestionsSwap();
    this.#tagActivationHandler = (event) => this.#handleTagActivation(event);
    this.#tagKeydownHandler = (event) => this.#handleTagKeydown(event);
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

    debugTagDetail("connected", {
      entryId: this.dataset?.entryId ?? null,
      tagDetailUrl: this.dataset?.tagDetailUrl ?? null,
    });
    listeners.add(
      this.#form,
      "htmx:configRequest",
      this.#configRequestHandler,
    );
    listeners.add(this.#form, "htmx:afterRequest", this.#afterRequestHandler);
    listeners.add(this, "htmx:afterSwap", this.#afterSwapHandler);
    listeners.add(this, "htmx:restored", this.#restoredHandler);
    listeners.add(this.#button, "click", this.#buttonClickHandler);
    listeners.add(this.#closeButton, "click", this.#closeClickHandler);
    listeners.add(this.#detailCloseButton, "click", this.#detailCloseClickHandler);
    listeners.add(this.#detailPopoverEl, "click", this.#detailClickHandler);
    listeners.add(this.#detailBody, "htmx:afterSwap", this.#detailAfterSwapHandler);
    listeners.add(
      this.#suggestions,
      "htmx:afterSwap",
      this.#suggestionsSwapHandler,
    );
    listeners.add(this.#tagContainer, "click", this.#tagActivationHandler);
    listeners.add(this.#tagContainer, "keydown", this.#tagKeydownHandler);
    listeners.add(window, "pageshow", this.#pageShowHandler);
    listeners.add(window, "pagehide", this.#pageHideHandler);
    listeners.add(document, "htmx:beforeHistorySave", this.#beforeHistorySaveHandler);
    listeners.add(document, "htmx:restored", this.#restoredHandler);
    listeners.add(document, "visibilitychange", this.#visibilityHandler);

    this.#initPopover();
    this.#updateSubmitState();
  }

  disconnectedCallback() {
    debugTagDetail("disconnected");
    this.#destroyPopover();
    this.#destroyDetailPopover();
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
    this.#detailPopoverEl = this.querySelector(".tag-detail-popover");
    this.#detailPanel = this.#detailPopoverEl?.querySelector(".tag-detail-panel") ?? null;
    this.#detailCloseButton = this.#detailPopoverEl?.querySelector(".overlay-close") ?? null;
    this.#detailBody = this.#detailPopoverEl?.querySelector(".tag-detail-body") ?? null;
    if (this.#detailBody && !this.#detailSkeleton) {
      this.#detailSkeleton = this.#detailBody.innerHTML;
    }
    this.#tagContainer = this.querySelector(".entry-tags-list");

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

  #destroyDetailPopover() {
    if (this.#detailPopover) {
      this.#detailPopover.destroy();
      this.#detailPopover = null;
    }
    this.#detailOutsideListeners = this.disposeListenerBag(
      this.#detailOutsideListeners
    );
    this.#clearActiveTag();
  }

  #togglePopover() {
    if (!this.#popover) return;
    if (this.#popover.isOpen) {
      this.#popover.hide();
    } else {
      this.#detailPopover?.hide();
      this.#popover.show();
    }
  }

  #handleCloseClick(event) {
    event.preventDefault();
    this.#popover?.hide();
  }

  #handleDetailCloseClick(event) {
    event.preventDefault();
    debugTagDetail("close button");
    this.#detailPopover?.hide();
  }

  #handleVisibility() {
    if (document.visibilityState === "visible") {
      this.#resetDetailPopoverState();
    }
  }

  #getExistingTags() {
    const seen = new Set();
    if (!this.#tagContainer) return seen;
    const limit = this.#getCanonicalMaxLength();
    this.#tagContainer.querySelectorAll(".tag-label").forEach((el) => {
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
      const tag = this.#tagContainer?.lastElementChild;
      if (tag?.classList?.contains("entry-tag")) {
        animateMotion(tag, "motion-animate-tag-enter");
        const limit = this.#getCanonicalMaxLength();
        const label = tag
          .querySelector(".tag-label")
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
    } else if (target.classList?.contains("tag-tombstone")) {
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

  #handleTagActivation(event) {
    const label = event.target.closest?.(".tag-label");
    if (!label || !(label instanceof HTMLElement)) return;

    if (event.target?.closest?.(".tag-remove")) {
      return;
    }

    event.preventDefault();
    debugTagDetail("tag activation", label.textContent?.trim());
    void this.#openTagDetail(label);
  }

  #handleTagKeydown(event) {
    if (![" ", "Enter"].includes(event.key)) return;
    const label = event.target.closest?.(".tag-label");
    if (!label) return;

    event.preventDefault();
    label.click();
  }

  async #openTagDetail(label) {
    if (!this.#detailPopoverEl || !this.#detailBody) {
      return;
    }
    const tagEl = label.closest(".entry-tag");
    const tagHash =
      label.dataset.tagHash || tagEl?.dataset?.tagHash || "";
    if (!tagHash) {
      return;
    }

    if (this.#detailPopover?.isOpen && this.#activeTagHash === tagHash) {
      debugTagDetail("toggle off", tagHash);
      this.#detailPopover.hide();
      return;
    }

    if (this.#detailPopover) {
      debugTagDetail("destroy existing popover before open", tagHash);
      this.#destroyDetailPopover();
    }

    this.#popover?.hide();
    this.#initDetailPopover(label);
    this.#loadTagDetail(tagHash);
    this.#detailPopover?.show();
    debugTagDetail("opened", tagHash);
  }

  #initDetailPopover(trigger) {
    this.#destroyDetailPopover();
    if (!this.#detailPopoverEl) {
      return;
    }
    const labelEl = trigger instanceof HTMLElement ? trigger : null;

    this.#detailPopover = createPopover(labelEl, this.#detailPopoverEl, {
      placement: "bottom-start",
      getPanel: () => this.#detailPanel,
      closeOnOutside: false,
      onShow: () => {
        this.classList.add("popover-open");
        if (labelEl) {
          labelEl.setAttribute("aria-expanded", "true");
          labelEl.classList.add("is-active");
          this.#activeTagLabel = labelEl;
          const tagEl = labelEl.closest(".entry-tag");
          if (tagEl) {
            tagEl.classList.add("is-active");
            this.#activeTagEl = tagEl;
          }
        }
        debugTagDetail("popover show", this.#activeTagHash);
        this.#registerDetailOutsideClose();
      },
      onHide: () => {
        this.#detailOutsideListeners = this.disposeListenerBag(
          this.#detailOutsideListeners
        );
        this.#clearActiveTag();
        debugTagDetail("popover hide", this.#activeTagHash);
      },
      onHidden: () => {
        this.classList.remove("popover-open");
        debugTagDetail("popover hidden", this.#activeTagHash);
      },
    });
  }

  #clearActiveTag() {
    if (this.#activeTagEl) {
      this.#activeTagEl.classList.remove("is-active");
    }
    if (this.#activeTagLabel) {
      this.#activeTagLabel.classList.remove("is-active");
      this.#activeTagLabel.setAttribute("aria-expanded", "false");
    }
    this.#activeTagEl = null;
    this.#activeTagLabel = null;
    this.#activeTagHash = "";
  }

  #loadTagDetail(tagHash) {
    if (!this.#detailBody) return;
    const template = this.dataset?.tagDetailUrl ?? "";
    if (!template) {
      return;
    }
    const url = template.replace("__TAG_HASH__", tagHash);
    if (!url) {
      return;
    }
    this.#activeTagHash = tagHash;
    this.#detailBody.innerHTML =
      this.#detailSkeleton || DEFAULT_TAG_DETAIL_SKELETON;
    this.#detailBody.setAttribute("hx-get", url);
    if (typeof htmx !== "undefined" && htmx?.ajax) {
      debugTagDetail("loading detail", url);
      htmx.ajax("GET", url, {
        target: this.#detailBody,
        swap: "innerHTML",
        source: this.#detailBody,
      });
    } else if (typeof htmx !== "undefined") {
      htmx.process(this.#detailBody);
      htmx.trigger(this.#detailBody, "tag-detail:show");
    }
  }

  #handleDetailClick(event) {
    const target = event.target;
    const item = target?.closest?.(".tag-detail__item");
    if (!item) return;
    const currentId = document.getElementById("entries")?.dataset?.date ?? "";
    const itemDate = item.dataset?.date ?? "";
    const targetId = item.dataset?.target ?? "";
    debugTagDetail("detail item click", item.getAttribute("href"));
    if (itemDate && currentId && itemDate === currentId && targetId) {
      event.preventDefault();
      this.#forceHideDetailPopover("detail item click same-day");
      scrollToHighlight(null, {
        targetId,
        pushHistory: true,
      });
      return;
    }
    this.#forceHideDetailPopover("detail item click navigate");
  }

  #handleDetailAfterSwap(event) {
    this.#detailPopover?.update();
    const target = event?.target;
    if (target?.classList?.contains("tag-detail__summary")) {
      this.#cacheTagSummary(target);
      debugTagDetail("detail summary swap");
      return;
    }
    if (this.#detailBody) {
      formatTimeElements(this.#detailBody);
      this.#hydrateSummaryFromCache();
    }
    debugTagDetail("detail after swap");
  }

  #registerDetailOutsideClose() {
    if (!this.#detailPopover) return;
    this.#detailOutsideListeners = this.resetListenerBag(
      this.#detailOutsideListeners
    );
    const listeners = this.#detailOutsideListeners;
    listeners.add(
      document,
      "click",
      (event) => {
        const target = event?.target;
        if (!(target instanceof Node)) {
          return;
        }
        if (this.contains(target)) {
          return;
        }
        debugTagDetail("outside click -> hide");
        this.#detailPopover?.hide();
      },
      true
    );
  }

  #forceHideDetailPopover(reason = "force") {
    debugTagDetail("force hide", reason);
    if (this.#detailPopover) {
      this.#detailPopover.destroy();
      this.#detailPopover = null;
    }
    if (this.#detailPopoverEl) {
      this.#detailPopoverEl.hidden = true;
      this.#detailPopoverEl.classList.remove("fade-enter", "fade-exit");
    }
    if (this.#detailPanel) {
      this.#detailPanel.classList.remove("pop-enter", "pop-exit");
    }
    this.#detailOutsideListeners = this.disposeListenerBag(
      this.#detailOutsideListeners
    );
    this.#clearActiveTag();
  }

  #resetDetailPopoverState(reason = "reset") {
    debugTagDetail("reset detail popover", reason);
    this.#forceHideDetailPopover(reason);
  }

  #getSummaryCacheKey(tagHash) {
    return `${TAG_SUMMARY_CACHE_PREFIX}${tagHash}`;
  }

  #getCachedTagSummary(tagHash) {
    if (!tagHash) return null;
    try {
      const raw = window.sessionStorage.getItem(this.#getSummaryCacheKey(tagHash));
      if (!raw) return null;
      const payload = JSON.parse(raw);
      if (!payload || typeof payload.html !== "string") return null;
      if (
        typeof payload.timestamp === "number" &&
        Date.now() - payload.timestamp > TAG_SUMMARY_CACHE_TTL
      ) {
        window.sessionStorage.removeItem(this.#getSummaryCacheKey(tagHash));
        return null;
      }
      return payload.html;
    } catch (error) {
      return null;
    }
  }

  #setCachedTagSummary(tagHash, html) {
    if (!tagHash || !html) return;
    try {
      const payload = JSON.stringify({
        html,
        timestamp: Date.now(),
      });
      window.sessionStorage.setItem(this.#getSummaryCacheKey(tagHash), payload);
    } catch (error) {
      return;
    }
  }

  #cacheTagSummary(summaryEl) {
    if (!(summaryEl instanceof HTMLElement)) return;
    const tagHash =
      summaryEl.dataset?.tagHash || this.#activeTagHash || "";
    const html = summaryEl.innerHTML?.trim();
    if (!tagHash || !html) return;
    if (html.includes("Summary unavailable")) {
      return;
    }
    this.#setCachedTagSummary(tagHash, html);
  }

  #hydrateSummaryFromCache() {
    if (!this.#detailBody) return;
    const summaryEl = this.#detailBody.querySelector(".tag-detail__summary");
    if (!summaryEl) return;
    const tagHash =
      summaryEl.dataset?.tagHash || this.#activeTagHash || "";
    if (!tagHash) return;
    const cached = this.#getCachedTagSummary(tagHash);
    if (cached) {
      summaryEl.innerHTML = cached;
      summaryEl.removeAttribute("hx-get");
      summaryEl.removeAttribute("hx-trigger");
      summaryEl.removeAttribute("hx-swap");
      summaryEl.removeAttribute("hx-disinherit");
      debugTagDetail("summary cache hit", tagHash);
      return;
    }
    if (typeof htmx !== "undefined") {
      debugTagDetail("summary cache miss -> load", tagHash);
      window.setTimeout(() => {
        htmx.trigger(summaryEl, "tag-detail:summary");
      }, 60);
    }
  }
}

const DEFAULT_TAG_DETAIL_SKELETON = `
  <div class="tag-detail-skeleton" aria-hidden="true">
    <span class="tag-detail-skeleton__title"></span>
    <span class="tag-detail-skeleton__meta"></span>
    <span class="tag-detail-skeleton__line"></span>
    <span class="tag-detail-skeleton__line"></span>
    <span class="tag-detail-skeleton__item"></span>
    <span class="tag-detail-skeleton__item"></span>
  </div>
`;

if (typeof customElements !== "undefined" && !customElements.get("entry-tags")) {
  customElements.define("entry-tags", EntryTags);
}
