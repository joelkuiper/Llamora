import { createPopover } from "../popover.js";
import { formatTimeElements } from "../services/time.js";
import { scrollToHighlight } from "../ui.js";
import { AutocompleteHistory } from "../utils/autocomplete-history.js";
import { parsePositiveInteger } from "../utils/number.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import { getValue, setValue } from "../services/lockbox-store.js";
import { animateMotion } from "../utils/transition.js";
import { AutocompleteOverlayMixin } from "./base/autocomplete-overlay.js";
import { syncSummarySkeletons } from "../services/summary-skeleton.js";

const canonicalizeTag = (value, limit = null) => {
  const raw = `${value ?? ""}`.trim().toLowerCase();
  if (!raw) return "";
  let text = raw.replace(/[\s_]+/g, "-");
  text = text.replace(/[^a-z0-9-]/g, "");
  text = text.replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
  if (!text) return "";
  if (Number.isFinite(limit) && limit > 0) {
    text = text.slice(0, limit).replace(/^-+|-+$/g, "");
  }
  return text.trim();
};

const displayTag = (canonical) => `${canonical ?? ""}`.trim();

const prepareTagAutocompleteValue = (value) => `${value ?? ""}`.trim();

const TAG_HISTORY_MAX = 50;
const TAG_SUGGESTION_EMPTY_DELAY_MS = 180;
const TAG_SKELETON_COUNT = 6;
const SUMMARY_NAMESPACE = "summary";

let sharedTagPopoverEl = null;
let sharedTagDetailPopoverEl = null;
let activeTagOwner = null;

const normalizeSummaryWords = (value) => {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

const makeTagSummaryKey = (tagHash, words) => {
  const base = `tag:${String(tagHash || "").trim()}`;
  if (!base || base === "tag:") return "";
  const count = normalizeSummaryWords(words);
  return count ? `${base}:w${count}` : base;
};

const readTagSummaryPayload = (payload, digest) => {
  if (!payload || typeof payload !== "object") return "";
  if (digest != null && String(payload.digest || "") !== String(digest)) return "";
  const value = payload.html;
  return typeof value === "string" ? value : "";
};

const getCachedTagSummary = async (tagHash, { digest, words } = {}) => {
  const key = makeTagSummaryKey(tagHash, words);
  if (!key) return "";
  const payload = await getValue(SUMMARY_NAMESPACE, key);
  return readTagSummaryPayload(payload, digest);
};

const setCachedTagSummary = async (tagHash, html, { digest, words } = {}) => {
  const key = makeTagSummaryKey(tagHash, words);
  if (!key || html == null) return false;
  return setValue(SUMMARY_NAMESPACE, key, {
    digest: String(digest ?? ""),
    html,
  });
};

const getSharedTagPopoverEl = () => {
  if (!sharedTagPopoverEl || !sharedTagPopoverEl.isConnected) {
    sharedTagPopoverEl = document.getElementById("tag-popover-global");
  }
  return sharedTagPopoverEl;
};

const getSharedTagDetailPopoverEl = () => {
  if (!sharedTagDetailPopoverEl || !sharedTagDetailPopoverEl.isConnected) {
    sharedTagDetailPopoverEl = document.getElementById("tag-detail-popover-global");
  }
  return sharedTagDetailPopoverEl;
};

export const mergeTagCandidateValues = (
  remoteCandidates = [],
  localCandidates = [],
  limit = null,
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
  #suggestionsSkeleton = "";
  #activeTagEl = null;
  #activeTagLabel = null;
  #activeTagHash = "";
  #detailOutsideListeners = null;
  #tagContainer = null;
  #listeners = null;
  #sharedListeners = null;
  #pendingDetailOpen = false;
  #inputListeners = null;
  #buttonClickHandler;
  #closeClickHandler;
  #detailCloseClickHandler;
  #detailClickHandler;
  #detailAfterSwapHandler;
  #rehydrateHandler;
  #teardownHandler;
  #inputHandler;
  #inputFocusHandler;
  #configRequestHandler;
  #afterRequestHandler;
  #afterSwapHandler;
  #suggestionsConfigHandler;
  #suggestionsBeforeSwapHandler;
  #suggestionsSwapHandler;
  #suggestionsSkeletonTimer = null;
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
    this.#rehydrateHandler = () => {
      this.#resetDetailPopoverState();
      this.#hideTagPopover("rehydrate");
      this.#resetSharedPopoverElement();
    };
    this.#teardownHandler = () => this.#forceHideDetailPopover("teardown");
    this.#inputHandler = () => {
      this.#updateSubmitState();
      this.scheduleAutocompleteFetch();
    };
    this.#inputFocusHandler = () => this.#handleInputFocus();
    this.#configRequestHandler = (event) => this.#handleConfigRequest(event);
    this.#afterRequestHandler = () => this.#handleAfterRequest();
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#suggestionsConfigHandler = (event) => this.#handleSuggestionsConfig(event);
    this.#suggestionsBeforeSwapHandler = (event) => this.#handleSuggestionsBeforeSwap(event);
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

    if (!this.#button || !this.#tagContainer) {
      return;
    }

    listeners.add(this, "htmx:afterSwap", this.#afterSwapHandler);
    listeners.add(this.#button, "click", this.#buttonClickHandler);
    listeners.add(this.#tagContainer, "click", this.#tagActivationHandler);
    listeners.add(this.#tagContainer, "keydown", this.#tagKeydownHandler);
    listeners.add(document, "app:rehydrate", this.#rehydrateHandler);
    listeners.add(document, "app:teardown", this.#teardownHandler);
    this.#updateSubmitState();
  }

  disconnectedCallback() {
    if (this.#isActiveOwner()) {
      this.#deactivateSharedOwner("disconnect");
    } else {
      this.#destroyPopover();
      this.#destroyDetailPopover();
      this.#detachSharedListeners();
    }
    this.#teardownListeners();
    this.#inputListeners = this.disposeListenerBag(this.#inputListeners);
    super.disconnectedCallback();
  }

  #cacheElements() {
    this.#button = this.querySelector(".add-tag-btn");
    this.#popoverEl = getSharedTagPopoverEl();
    this.#form = this.#popoverEl?.querySelector("form") ?? null;
    this.#input = this.#form?.querySelector('input[name="tag"]') ?? null;
    this.#submit = this.#form?.querySelector('button[type="submit"]') ?? null;
    this.#panel = this.#popoverEl?.querySelector(".tp-content") ?? null;
    this.#suggestions = this.#popoverEl?.querySelector(".tag-suggestions") ?? null;
    this.#closeButton = this.#popoverEl?.querySelector(".overlay-close") ?? null;
    this.#detailPopoverEl = getSharedTagDetailPopoverEl();
    this.#detailPanel = this.#detailPopoverEl?.querySelector(".tag-detail-panel") ?? null;
    this.#detailCloseButton = this.#detailPopoverEl?.querySelector(".overlay-close") ?? null;
    this.#detailBody = this.#detailPopoverEl?.querySelector(".tag-detail-body") ?? null;
    if (this.#detailBody && !this.#detailSkeleton) {
      this.#detailSkeleton = this.#detailBody.innerHTML;
    }
    this.#ensureSkeleton();
    this.#tagContainer = this.querySelector(".entry-tags-list");

    this.#button?.setAttribute("aria-expanded", "false");
  }

  #teardownListeners() {
    this.#listeners = this.disposeListenerBag(this.#listeners);
  }

  #isActiveOwner() {
    return activeTagOwner === this;
  }

  #activateSharedOwner() {
    if (activeTagOwner && activeTagOwner !== this) {
      activeTagOwner.#deactivateSharedOwner("switch");
    }
    activeTagOwner = this;
    this.#attachSharedListeners();
  }

  #deactivateSharedOwner(reason = "deactivate") {
    if (this.#popover) {
      this.#popover.hide();
    }
    this.#forceHideDetailPopover(reason);
    this.#destroyPopover();
    this.#detachSharedListeners();
    this.refreshAutocompleteController({
      force: true,
      reason: "shared-release",
    });
    if (activeTagOwner === this) {
      activeTagOwner = null;
    }
  }

  #maybeReleaseSharedOwner() {
    if (!this.#isActiveOwner()) {
      return;
    }
    if (this.#pendingDetailOpen) {
      return;
    }
    const popoverOpen = this.#popover?.isOpen;
    const detailOpen = this.#detailPopover?.isOpen;
    if (popoverOpen || detailOpen) {
      return;
    }
    this.#detachSharedListeners();
    this.refreshAutocompleteController({
      force: true,
      reason: "shared-release",
    });
    if (activeTagOwner === this) {
      activeTagOwner = null;
    }
  }

  #attachSharedListeners() {
    if (!this.#form || !this.#popoverEl) {
      return;
    }
    this.#sharedListeners = this.resetListenerBag(this.#sharedListeners);
    const listeners = this.#sharedListeners;
    listeners.add(this.#form, "htmx:configRequest", this.#configRequestHandler);
    listeners.add(this.#form, "htmx:afterRequest", this.#afterRequestHandler);
    if (this.#closeButton) {
      listeners.add(this.#closeButton, "click", this.#closeClickHandler);
    }
    if (this.#detailCloseButton) {
      listeners.add(this.#detailCloseButton, "click", this.#detailCloseClickHandler);
    }
    if (this.#detailPopoverEl) {
      listeners.add(this.#detailPopoverEl, "click", this.#detailClickHandler);
      listeners.add(this.#detailPopoverEl, "htmx:afterRequest", (event) => {
        const trigger = event?.detail?.elt;
        if (!trigger?.classList?.contains("tag-detail__remove")) return;
        const status = event?.detail?.xhr?.status ?? 0;
        if (status >= 200 && status < 300) {
          this.#forceHideDetailPopover("detail remove");
        }
      });
    }
    if (this.#detailBody) {
      listeners.add(this.#detailBody, "htmx:afterSwap", this.#detailAfterSwapHandler);
    }
    if (this.#suggestions) {
      listeners.add(this.#suggestions, "htmx:afterSwap", this.#suggestionsSwapHandler);
      listeners.add(this.#suggestions, "htmx:configRequest", this.#suggestionsConfigHandler);
      listeners.add(this.#suggestions, "htmx:beforeSwap", this.#suggestionsBeforeSwapHandler);
    }
  }

  #detachSharedListeners() {
    this.#sharedListeners = this.disposeListenerBag(this.#sharedListeners);
  }

  #prepareSharedPopover() {
    if (!this.#form || !this.#suggestions || !this.#input) return;
    const entryId = this.dataset?.entryId ?? "";
    const addUrl = this.dataset?.addTagUrl ?? "";
    if (addUrl) {
      this.#form.setAttribute("hx-post", addUrl);
    }
    if (entryId) {
      this.#form.setAttribute("hx-target", `#entry-tags-${entryId}`);
    }
    this.#form.setAttribute("hx-swap", "beforeend");
    this.#form.reset();
    this.#input.disabled = false;
    if (this.#suggestions) {
      const suggestionUrl = this.dataset?.suggestionsUrl ?? "";
      if (suggestionUrl) {
        this.#suggestions.setAttribute("hx-get", suggestionUrl);
      }
      this.#resetSuggestions({ force: true, clearEntry: false });
      this.#suggestions.dataset.entryId = entryId;
    }
    this.#updateSubmitState();
    if (typeof htmx !== "undefined") {
      htmx.process(this.#popoverEl);
    }
  }

  #resetSuggestions({ force = false, clearEntry = false } = {}) {
    if (!this.#suggestions) return;
    if (!force && this.#suggestions.dataset.entryId === undefined) return;
    this.#ensureSkeleton();
    this.#clearSkeletonTimer();
    if (this.#shouldUseEmptySuggestions()) {
      this.#suggestions.innerHTML = "";
    } else {
      this.#suggestions.innerHTML = "";
      this.#suggestionsSkeletonTimer = window.setTimeout(() => {
        if (!this.#suggestions) return;
        if (this.#shouldUseEmptySuggestions()) return;
        if (this.#suggestions.innerHTML.trim()) return;
        this.#suggestions.innerHTML = this.#suggestionsSkeleton;
      }, TAG_SUGGESTION_EMPTY_DELAY_MS);
    }
    this.#suggestions.classList.remove("htmx-swapping", "htmx-settling", "htmx-request");
    this.#suggestions.querySelectorAll(".tag-suggestion").forEach((el) => {
      el.classList.remove("htmx-added", "htmx-settling", "htmx-swapping");
    });
    delete this.#suggestions.dataset.loaded;
    delete this.#suggestions.dataset.requestEntryId;
    if (clearEntry) {
      delete this.#suggestions.dataset.entryId;
      this.#clearEmptySuggestionCache();
    }
  }

  #hideTagPopover(reason = "hide") {
    if (this.#popover) {
      this.#popover.hide();
      this.#destroyPopover();
    }
    if (this.#isActiveOwner()) {
      this.#deactivateSharedOwner(reason);
    }
    this.#button?.setAttribute("aria-expanded", "false");
    this.classList.remove("popover-open");
  }

  #resetSharedPopoverElement() {
    const pop = getSharedTagPopoverEl();
    if (!pop) return;
    pop.hidden = true;
    pop.removeAttribute("data-floating-ui-placement");
    pop.style.left = "";
    pop.style.top = "";
    pop.style.inset = "";
    pop.style.transform = "";
    pop.style.margin = "";
    pop.classList.remove("htmx-swapping", "htmx-settling", "htmx-request");
    const panel = pop.querySelector(".tp-content");
    panel?.classList.remove("fade-enter", "fade-exit", "pop-enter", "pop-exit");
    if (activeTagOwner === this) {
      activeTagOwner = null;
    }
  }

  #ensureSkeleton() {
    if (!this.#suggestions) return;
    // Prefer template to avoid drift between server and client markup.
    const tpl = document.getElementById("tag-suggestions-skeleton-template");
    if (tpl?.innerHTML?.trim()) {
      this.#suggestionsSkeleton = tpl.innerHTML.trim();
      return;
    }
    this.#suggestionsSkeleton = Array.from({ length: TAG_SKELETON_COUNT })
      .map(() => '<span class="tag-suggestion tag-suggestion--skeleton" aria-hidden="true"></span>')
      .join("");
  }

  #initPopover() {
    this.#destroyPopover();
    this.#cacheElements();
    if (!this.#button || !this.#popoverEl) {
      return;
    }

    this.#popover = createPopover(this.#button, this.#popoverEl, {
      getPanel: () => this.#panel,
      onBeforeShow: () => {
        this.#activateSharedOwner();
        this.#prepareSharedPopover();
        this.refreshAutocompleteController({
          force: true,
          reason: "tag-popover-show",
        });
      },
      onShow: () => {
        this.#button.classList.add("active");
        this.#button?.setAttribute("aria-expanded", "true");
        this.classList.add("popover-open");
        if (this.#suggestions) {
          this.#resetSuggestions({ force: true });
          if (!this.#shouldUseEmptySuggestions()) {
            htmx.trigger(this.#suggestions, "tag-popover:show");
          }
        }
        if (this.#input && typeof this.#input.focus === "function") {
          try {
            this.#input.focus({ preventScroll: true });
          } catch (_error) {
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
        this.#maybeReleaseSharedOwner();
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
    this.#detailOutsideListeners = this.disposeListenerBag(this.#detailOutsideListeners);
    this.#clearActiveTag();
  }

  #togglePopover() {
    if (this.#popover?.isOpen) {
      this.#popover.hide();
      return;
    }
    this.#destroyPopover();
    this.#initPopover();
    if (!this.#popover) return;
    this.#detailPopover?.hide();
    this.#popover.show();
  }

  #handleCloseClick(event) {
    event.preventDefault();
    this.#popover?.hide();
  }

  #handleDetailCloseClick(event) {
    event.preventDefault();
    this.#detailPopover?.hide();
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
    if (!this.#isActiveOwner()) {
      return false;
    }
    if (!this.#input) {
      return false;
    }
    if (this.#popover?.isOpen) {
      return true;
    }
    if (typeof document !== "undefined" && document.activeElement === this.#input) {
      return true;
    }
    return false;
  }

  #handleInputFocus() {
    if (!this.#isActiveOwner()) {
      return;
    }
    this.scheduleAutocompleteFetch({ immediate: true });
    this.#updateAutocompleteCandidates();
  }

  #updateSubmitState() {
    if (!this.#isActiveOwner()) {
      return;
    }
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
    if (!this.#isActiveOwner()) {
      return;
    }
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
    if (!this.#isActiveOwner()) {
      return;
    }
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
        const label = tag.querySelector(".tag-label")?.textContent?.trim();
        const canonicalValue = canonicalizeTag(label ?? "", limit);
        const canonicalKey = canonicalValue?.toLowerCase();
        if (canonicalKey && this.#suggestions) {
          this.#suggestions.querySelectorAll(".tag-suggestion").forEach((btn) => {
            const btnCanonical = canonicalizeTag(
              btn.dataset.tag ?? btn.textContent ?? "",
              limit,
            )?.toLowerCase();
            if (btnCanonical === canonicalKey) {
              btn.remove();
            }
          });
        }
        if (canonicalValue) {
          this.#tagHistory.add(canonicalValue);
          this.setAutocompleteLocalEntries("history", this.#tagHistory.values());
          this.applyAutocompleteCandidates();
        }
        this.#clearEmptySuggestionCache();
      }
      this.#updateSubmitState();
      this.#invalidateAutocompleteCache({ immediate: true });
    } else if (target.classList?.contains("tag-tombstone")) {
      target.remove();
      this.#updateSubmitState();
      this.#invalidateAutocompleteCache({ immediate: true });
      this.#clearEmptySuggestionCache();
    }
  }

  #handleSuggestionsSwap() {
    if (!this.#isActiveOwner()) {
      return;
    }
    if (!this.#suggestions) return;
    this.#clearSkeletonTimer();
    delete this.#suggestions.dataset.requestEntryId;
    if (this.#suggestions.innerHTML.trim()) {
      this.#suggestions.dataset.loaded = "1";
      this.#clearEmptySuggestionCache();
    } else {
      delete this.#suggestions.dataset.loaded;
      this.#setEmptySuggestionCache();
      this.#suggestions.innerHTML = "";
    }
    this.#popover?.update();
    this.#updateAutocompleteCandidates();
  }

  #shouldUseEmptySuggestions() {
    if (!this.#suggestions) return false;
    const entryId = this.dataset?.entryId ?? "";
    const cachedEntry = this.#suggestions.dataset.emptyEntryId ?? "";
    return this.#suggestions.dataset.empty === "1" && entryId && entryId === cachedEntry;
  }

  #setEmptySuggestionCache() {
    if (!this.#suggestions) return;
    const entryId = this.dataset?.entryId ?? "";
    if (!entryId) return;
    this.#suggestions.dataset.empty = "1";
    this.#suggestions.dataset.emptyEntryId = entryId;
  }

  #clearEmptySuggestionCache() {
    if (!this.#suggestions) return;
    delete this.#suggestions.dataset.empty;
    delete this.#suggestions.dataset.emptyEntryId;
  }

  #clearSkeletonTimer() {
    if (this.#suggestionsSkeletonTimer) {
      clearTimeout(this.#suggestionsSkeletonTimer);
      this.#suggestionsSkeletonTimer = null;
    }
  }

  #handleSuggestionsConfig(event) {
    if (!this.#isActiveOwner() || !this.#suggestions) return;
    const entryId = this.dataset?.entryId ?? "";
    this.#suggestions.dataset.requestEntryId = entryId;
    if (event?.detail?.headers) {
      event.detail.headers["X-Tag-Entry"] = entryId;
    }
  }

  #handleSuggestionsBeforeSwap(event) {
    if (!this.#isActiveOwner() || !this.#suggestions) return;
    const reqEntry = this.#suggestions.dataset.requestEntryId ?? "";
    const currentEntry = this.#suggestions.dataset.entryId ?? "";
    if (reqEntry && currentEntry && reqEntry !== currentEntry) {
      event.preventDefault();
    }
  }

  getAutocompleteControllerOptions() {
    return {
      minLength: 2,
      prepareQuery: prepareTagAutocompleteValue,
      prepareCandidate: prepareTagAutocompleteValue,
      prefixOnly: true,
    };
  }

  getAutocompleteInputConfig() {
    if (!this.#isActiveOwner()) {
      return null;
    }
    return {
      resolve: () => this.#input ?? null,
      observe: false,
    };
  }

  getAutocompleteStoreOptions() {
    return {
      debounceMs: 200,
      fetchCandidates: (query, context = {}) =>
        this.#fetchTagAutocompleteCandidates(query, context),
      buildCacheKey: (query, context = {}) => this.#buildAutocompleteCacheKey(query, context),
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
        const raw = typeof item === "string" ? item : (item?.value ?? "");
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

  onAutocompleteInputChanged(input, _previous, meta = {}) {
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
    if (!this.#isActiveOwner()) {
      return;
    }
    const domValues = [];
    if (this.#suggestions) {
      this.#suggestions.querySelectorAll(".tag-suggestion").forEach((btn) => {
        const text = btn.textContent?.trim();
        if (!text) {
          return;
        }
        const canonical = canonicalizeTag(btn.dataset.tag ?? text, this.#getCanonicalMaxLength());
        if (!canonical) return;
        domValues.push(canonical);
      });
    }
    this.setAutocompleteLocalEntries("dom", domValues);
    this.setAutocompleteLocalEntries("history", this.#tagHistory.values());
    this.applyAutocompleteCandidates();
  }

  #invalidateAutocompleteCache({ immediate = false } = {}) {
    if (!this.#isActiveOwner()) {
      return;
    }
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
    const normalized = canonical ? canonical.toLowerCase() : (query ?? "").trim().toLowerCase();
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

  #getTagClickMode() {
    const mode = String(this.dataset?.tagClickMode || "")
      .trim()
      .toLowerCase();
    return mode === "navigate" ? "navigate" : "detail";
  }

  #getTagName(label) {
    if (!(label instanceof HTMLElement)) {
      return "";
    }
    return String(label.dataset?.tagName || label.textContent || "").trim();
  }

  #resolveTagTemplate(template, tagName) {
    const raw = String(template || "").trim();
    const tag = String(tagName || "").trim();
    if (!raw || !tag) {
      return "";
    }
    return raw.replaceAll("__TAG__", encodeURIComponent(tag));
  }

  #navigateToTag(tagName) {
    const normalizedTag = String(tagName || "").trim();
    if (!normalizedTag) {
      return;
    }
    document.dispatchEvent(
      new CustomEvent("tags-view:navigate", { detail: { tag: normalizedTag } }),
    );

    const fragmentTemplate = this.dataset?.tagNavigateFragmentTemplate || "";
    const pageTemplate = this.dataset?.tagNavigatePageTemplate || "";
    const fragmentUrl = this.#resolveTagTemplate(fragmentTemplate, normalizedTag);
    const pageUrl = this.#resolveTagTemplate(pageTemplate, normalizedTag);
    const target = document.getElementById("tags-view-detail");

    if (fragmentUrl && target instanceof HTMLElement && typeof htmx !== "undefined") {
      htmx.ajax("GET", fragmentUrl, {
        target,
        swap: "outerHTML",
        pushURL: pageUrl || fragmentUrl,
      });
      return;
    }

    if (pageUrl) {
      window.location.assign(pageUrl);
    }
  }

  #handleTagActivation(event) {
    const label = event.target.closest?.(".tag-label");
    if (!label || !(label instanceof HTMLElement)) return;

    if (event.target?.closest?.(".tag-remove")) {
      return;
    }

    if (label instanceof HTMLAnchorElement && this.#getTagClickMode() === "navigate") {
      document.dispatchEvent(
        new CustomEvent("tags-view:navigate", {
          detail: { tag: this.#getTagName(label) },
        }),
      );
      return;
    }

    event.preventDefault();
    if (this.#getTagClickMode() === "navigate") {
      this.#navigateToTag(this.#getTagName(label));
      return;
    }
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
    this.#cacheElements();
    if (!this.#detailPopoverEl || !this.#detailBody) {
      return;
    }
    this.#activateSharedOwner();
    const tagEl = label.closest(".entry-tag");
    const tagHash = label.dataset.tagHash || tagEl?.dataset?.tagHash || "";
    if (!tagHash) {
      return;
    }

    if (this.#detailPopover?.isOpen && this.#activeTagHash === tagHash) {
      this.#detailPopover.hide();
      return;
    }

    if (this.#detailPopover) {
      this.#destroyDetailPopover();
    }

    this.#pendingDetailOpen = true;
    this.#popover?.hide();
    this.#initDetailPopover(label);
    this.#loadTagDetail(tagHash);
    this.#detailPopover?.show();
  }

  #initDetailPopover(trigger) {
    this.#destroyDetailPopover();
    this.#cacheElements();
    if (!this.#detailPopoverEl) {
      return;
    }
    const labelEl = trigger instanceof HTMLElement ? trigger : null;

    this.#detailPopover = createPopover(labelEl, this.#detailPopoverEl, {
      placement: "bottom-start",
      getPanel: () => this.#detailPanel,
      closeOnOutside: false,
      onBeforeShow: () => {
        this.#activateSharedOwner();
      },
      onShow: () => {
        this.classList.add("popover-open");
        this.#pendingDetailOpen = false;
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
        this.#registerDetailOutsideClose();
      },
      onHide: () => {
        this.#detailOutsideListeners = this.disposeListenerBag(this.#detailOutsideListeners);
        this.#clearActiveTag();
      },
      onHidden: () => {
        this.classList.remove("popover-open");
        this.#pendingDetailOpen = false;
        this.#maybeReleaseSharedOwner();
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
    let url = template.replace("__TAG_HASH__", tagHash);
    const entryId = this.dataset?.entryId ?? "";
    if (entryId) {
      const separator = url.includes("?") ? "&" : "?";
      url = `${url}${separator}entry_id=${encodeURIComponent(entryId)}`;
    }
    const day = document.getElementById("entries")?.dataset?.date?.trim() ?? "";
    if (day) {
      const separator = url.includes("?") ? "&" : "?";
      url = `${url}${separator}day=${encodeURIComponent(day)}`;
    }
    try {
      const currentUrl = new URL(window.location.href);
      const params = new URLSearchParams();
      const currentView = currentUrl.searchParams.get("view");
      if (currentView) {
        params.set("view", currentView);
      }
      const contextDay =
        document.getElementById("tags-view")?.dataset?.day?.trim() ||
        document.getElementById("entries")?.dataset?.date?.trim() ||
        currentUrl.pathname.match(/\/d\/(\d{4}-\d{2}-\d{2})$/)?.[1] ||
        "";
      if (contextDay) {
        params.set("day", contextDay);
      }
      for (const key of ["sort_kind", "sort_dir", "entries_limit", "tag", "target"]) {
        const value = currentUrl.searchParams.get(key);
        if (value) {
          params.set(key, value);
        }
      }
      const serialized = params.toString();
      if (serialized) {
        const separator = url.includes("?") ? "&" : "?";
        url = `${url}${separator}${serialized}`;
      }
    } catch (_error) {
      // Ignore malformed location state and continue with base URL.
    }
    if (!url) {
      return;
    }
    this.#activeTagHash = tagHash;
    this.#detailBody.innerHTML = this.#detailSkeleton || getDetailSkeletonMarkup();
    this.#detailBody.dataset.resetScroll = "1";
    this.#detailBody.setAttribute("hx-get", url);
    if (typeof htmx !== "undefined" && htmx?.ajax) {
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
    if (!this.#isActiveOwner()) {
      return;
    }
    const target = event.target;
    const goto = target?.closest?.(".tag-detail__goto");
    if (goto) {
      this.#forceHideDetailPopover("detail goto");
      return;
    }
    const item = target?.closest?.(".tag-detail__item");
    if (!item) return;
    const currentId = document.getElementById("entries")?.dataset?.date ?? "";
    const itemDate = item.dataset?.date ?? "";
    const targetId = item.dataset?.target ?? "";
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
    if (!this.#isActiveOwner()) {
      return;
    }
    if (this.#detailBody) {
      syncSummarySkeletons(this.#detailBody);
    }
    this.#detailPopover?.update();
    const target = event?.detail?.target ?? event?.target;
    const summaryEl =
      target?.closest?.(".tag-detail__summary") ||
      (target?.classList?.contains("tag-detail__summary") ? target : null);
    if (summaryEl) {
      this.#cacheTagSummary(summaryEl);
      return;
    }
    if (this.#detailBody) {
      formatTimeElements(this.#detailBody);
      if (target === this.#detailBody) {
        void this.#hydrateSummaryFromCache();
      }
      const entriesList = this.#detailBody.querySelector(".tag-detail__entries");
      if (entriesList && this.#detailBody.dataset.resetScroll === "1") {
        entriesList.scrollTop = 0;
        delete this.#detailBody.dataset.resetScroll;
      }
    }
  }

  #registerDetailOutsideClose() {
    if (!this.#detailPopover) return;
    this.#detailOutsideListeners = this.resetListenerBag(this.#detailOutsideListeners);
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
        if (this.#detailPopoverEl?.contains(target)) {
          return;
        }
        this.#detailPopover?.hide();
      },
      true,
    );
  }

  #forceHideDetailPopover(_reason = "force") {
    if (!this.#isActiveOwner()) {
      return;
    }
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
    this.#detailOutsideListeners = this.disposeListenerBag(this.#detailOutsideListeners);
    this.#clearActiveTag();
    this.#pendingDetailOpen = false;
  }

  #resetDetailPopoverState(reason = "reset") {
    this.#forceHideDetailPopover(reason);
  }

  #cacheTagSummary(summaryEl) {
    if (!(summaryEl instanceof HTMLElement)) return;
    const tagHash = summaryEl.dataset?.tagHash || this.#activeTagHash || "";
    const html = summaryEl.innerHTML?.trim();
    const summaryDigest = summaryEl.dataset?.summaryDigest || "";
    const summaryWords = summaryEl.dataset?.summaryWords || "";
    if (!tagHash || !html) return;
    if (html.includes("Summary unavailable")) {
      return;
    }
    void setCachedTagSummary(tagHash, html, {
      digest: summaryDigest,
      words: summaryWords,
    });
  }

  async #hydrateSummaryFromCache() {
    if (!this.#detailBody) return;
    const summaryEl = this.#detailBody.querySelector(".tag-detail__summary");
    if (!summaryEl) return;
    const tagHash = summaryEl.dataset?.tagHash || this.#activeTagHash || "";
    const summaryDigest = summaryEl.dataset?.summaryDigest || "";
    const summaryWords = summaryEl.dataset?.summaryWords || "";
    if (!tagHash) return;
    const cached = await getCachedTagSummary(tagHash, {
      digest: summaryDigest,
      words: summaryWords,
    });
    if (cached) {
      summaryEl.innerHTML = cached;
      summaryEl.removeAttribute("hx-get");
      summaryEl.removeAttribute("hx-trigger");
      summaryEl.removeAttribute("hx-swap");
      summaryEl.removeAttribute("hx-disinherit");
      return;
    }
    if (typeof htmx !== "undefined") {
      window.setTimeout(() => {
        htmx.trigger(summaryEl, "tag-detail:summary");
      }, 60);
    }
  }
}

const getDetailSkeletonMarkup = () => {
  const tpl = document.getElementById("summary-skeleton-template");
  if (tpl?.innerHTML?.trim()) {
    return tpl.innerHTML.trim();
  }
  return '<div class="tag-detail-skeleton" aria-hidden="true"><span class="tag-detail-skeleton__line"></span><span class="tag-detail-skeleton__line"></span><span class="tag-detail-skeleton__line"></span></div>';
};

if (typeof customElements !== "undefined" && !customElements.get("entry-tags")) {
  customElements.define("entry-tags", EntryTags);
}
