import { scrollToHighlight, createInlineSpinner } from "../ui.js";
import { prefersReducedMotion } from "../utils/motion.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import { createShortcutBag } from "../utils/global-shortcuts.js";
import { AutocompleteOverlayMixin } from "./base/autocomplete-overlay.js";
import { AutocompleteHistory } from "../utils/autocomplete-history.js";

const getEventTarget = (evt) => {
  const target = evt.target;
  if (target instanceof Element) {
    return target;
  }
  return target?.parentElement ?? null;
};

const RECENT_REFRESH_MIN_MS = 5000;
const RECENT_CANDIDATE_MAX = 50;

const normalizeSearchValue = (value) => {
  if (typeof value !== "string") return "";
  return value.trim().replace(/\s+/g, " ");
};

const buildSearchEntry = (value) => {
  const trimmed = typeof value === "string" ? value.trim() : "";
  if (!trimmed) return null;

  const collapsed = normalizeSearchValue(trimmed);
  const tokens = new Set([trimmed]);
  if (collapsed) {
    tokens.add(collapsed);
    collapsed
      .split(/\s+/)
      .filter(Boolean)
      .forEach((part) => tokens.add(part));
  }

  const tokenList = Array.from(tokens).filter(Boolean);
  tokenList.sort((a, b) => a.length - b.length || a.localeCompare(b));

  return {
    value: trimmed,
    display: trimmed,
    tokens: tokenList.length ? tokenList : [trimmed],
  };
};

export class SearchOverlay extends AutocompleteOverlayMixin(ReactiveElement) {
  #listeners = null;
  #overlayListeners = null;
  #resultsEl = null;
  #inputEl = null;
  #formEl = null;
  #spinnerEl = null;
  #spinnerController = null;
  #inputListeners = null;
  #inputHandler;
  #submitHandler;
  #keydownHandler;
  #documentClickHandler;
  #focusHandler;
  #pageShowHandler;
  #pageHideHandler;
  #historyRestoreHandler;
  #popStateHandler;
  #historyRestoreRemover = null;
  #recentHistory;
  #shortcutBag = null;
  #streamSource = null;
  #streamTimer = null;
  #streamQuery = "";

  constructor() {
    super();
    this.#inputHandler = () => this.#handleInput();
    this.#submitHandler = (event) => this.#handleSubmit(event);
    this.#keydownHandler = (event) => this.#handleKeydown(event);
    this.#documentClickHandler = (event) => this.#handleDocumentClick(event);
    this.#focusHandler = () => this.#handleInputFocus();
    this.#pageShowHandler = (event) => this.#handlePageShow(event);
    this.#pageHideHandler = (event) => this.#handlePageHide(event);
    this.#historyRestoreHandler = () => this.#handleHistoryRestore();
    this.#popStateHandler = () => this.#handlePopState();
    this.#recentHistory = new AutocompleteHistory({
      maxEntries: RECENT_CANDIDATE_MAX,
      normalize: (entry) => this.#normalizeCandidateValue(entry),
      prepare: (entry) => entry,
    });
  }

  connectedCallback() {
    super.connectedCallback();
    this.#resultsEl = this.querySelector("#search-results");
    this.#spinnerEl = this.querySelector("#search-spinner");
    this.#formEl = this.querySelector("#search-form");
    if (!this.#spinnerController) {
      this.#spinnerController = createInlineSpinner(this.#spinnerEl);
    } else {
      this.#spinnerController.setElement(this.#spinnerEl);
    }
    this.#deactivateOverlayListeners();

    this.#listeners = this.resetListenerBag(this.#listeners);
    const listeners = this.#listeners;

    this.#refreshInputState({
      forceAutocomplete: true,
      forceRecent: true,
      reason: "connected",
    });

    if (this.#formEl) {
      this.#listeners.add(this.#formEl, "submit", this.#submitHandler);
    }

    this.#registerShortcuts();

    const eventTarget = this.ownerDocument ?? document;

    if (this.#historyRestoreRemover) {
      this.#historyRestoreRemover();
      this.#historyRestoreRemover = null;
    }
    eventTarget.addEventListener("htmx:historyRestore", this.#historyRestoreHandler);
    this.#historyRestoreRemover = () => {
      eventTarget.removeEventListener(
        "htmx:historyRestore",
        this.#historyRestoreHandler,
      );
    };

    const win = eventTarget.defaultView ?? window;
    win.addEventListener("pageshow", this.#pageShowHandler);
    win.addEventListener("pagehide", this.#pageHideHandler);
    win.addEventListener("popstate", this.#popStateHandler);
  }

  disconnectedCallback() {
    this.#closeResults(false, { immediate: true });

    this.#deactivateOverlayListeners();
    this.#listeners = this.disposeListenerBag(this.#listeners);

    this.#spinnerController?.stop();
    this.#spinnerController?.setElement(null);
    this.#spinnerController = null;
    this.#shortcutBag?.abort();
    this.#shortcutBag = null;
    this.cancelAutocompleteFetch();
    this.#resultsEl = null;
    this.#inputEl = null;
    this.#formEl = null;
    this.#spinnerEl = null;
    this.#inputListeners = this.disposeListenerBag(this.#inputListeners);
    this.#closeSearchStream();
    if (this.#streamTimer) {
      window.clearTimeout(this.#streamTimer);
      this.#streamTimer = null;
    }

    const doc = this.ownerDocument ?? document;
    const win = doc.defaultView ?? window;
    win.removeEventListener("pageshow", this.#pageShowHandler);
    win.removeEventListener("pagehide", this.#pageHideHandler);
    win.removeEventListener("popstate", this.#popStateHandler);

    if (this.#historyRestoreRemover) {
      this.#historyRestoreRemover();
      this.#historyRestoreRemover = null;
    }
    super.disconnectedCallback();
  }

  #registerShortcuts() {
    if (!this.isConnected) return;
    this.#shortcutBag?.abort();
    this.#shortcutBag = createShortcutBag();
    if (!this.#inputEl) {
      return;
    }

    this.#shortcutBag.add({
      key: "/",
      handler: () => {
        this.#inputEl?.focus({ preventScroll: true });
        if (this.#inputEl) {
          this.#inputEl.select();
        }
      },
      preventDefault: true,
    });
  }

  #activateOverlayListeners() {
    if (!this.isConnected) return;

    this.#overlayListeners = this.disposeListenerBag(this.#overlayListeners);
    const bag = this.createListenerBag();
    const doc = this.ownerDocument ?? document;
    bag.add(doc, "keydown", this.#keydownHandler);
    bag.add(doc, "click", this.#documentClickHandler);
    this.#overlayListeners = bag;
  }

  #deactivateOverlayListeners() {
    this.#overlayListeners = this.disposeListenerBag(this.#overlayListeners);
  }

  #handleAfterSwap(evt) {
    const wrap = this.#resultsEl;
    const target = evt.detail?.target;
    if (!wrap || !(target instanceof Element)) return;

    if (target !== wrap) {
      if (wrap.contains(target) && wrap.classList.contains("is-open")) {
      }
      return;
    }


    const panel = wrap.querySelector(".sr-panel");
    if (!panel) {
      wrap.classList.remove("is-open");
      this.#deactivateOverlayListeners();
      this.#addCurrentQueryToAutocomplete();
      this.#loadRecentSearches();
      return;
    }

    if (wrap.classList.contains("is-open")) {
      panel.classList.remove("htmx-added");
      this.#addCurrentQueryToAutocomplete();
      this.#loadRecentSearches();
      return;
    }

    const completeEnter = () => {
      panel.classList.remove("pop-enter");
      wrap.classList.add("is-open");
      this.#activateOverlayListeners();
    };

    panel.classList.add("pop-enter");

    if (prefersReducedMotion()) {
      completeEnter();
    } else {
      panel.addEventListener("animationend", completeEnter, { once: true });
    }
    this.#addCurrentQueryToAutocomplete();
    this.#loadRecentSearches();
  }

  #handleInput() {
    if (!this.#inputEl) return;
    const value = this.#inputEl.value.trim();
    if (!value) {
      this.#closeResults();
      return;
    }
    this.#scheduleSearchStream();
  }

  #handleInputFocus() {
    this.#refreshInputState();
  }

  #handleSubmit(event) {
    if (event?.preventDefault) {
      event.preventDefault();
    }
    this.#startSearchStream();
  }

  #scheduleSearchStream() {
    if (this.#streamTimer) {
      window.clearTimeout(this.#streamTimer);
    }
    this.#streamTimer = window.setTimeout(() => {
      this.#streamTimer = null;
      this.#startSearchStream();
    }, 300);
  }

  #startSearchStream() {
    if (!this.#inputEl) return;
    const query = normalizeSearchValue(this.#inputEl.value);
    if (!query) {
      this.#closeResults();
      return;
    }

    if (this.#streamSource && this.#streamQuery === query) {
      return;
    }

    this.#closeSearchStream();
    this.#streamQuery = query;

    const url = `/search/stream?q=${encodeURIComponent(query)}`;
    const source = new EventSource(url, { withCredentials: true });
    this.#streamSource = source;
    this.#spinnerController?.start();
    if (this.#resultsEl) {
      this.#resultsEl.setAttribute("aria-busy", "true");
      this.#resultsEl.classList.add("is-streaming");
    }

    source.addEventListener("open", () => {
      console.debug("[search] stream open", { url });
    });

    source.addEventListener("initial", (event) => {
      if (!this.#resultsEl) return;
      const html = event.data || "";
      this.#applyStreamInitial(html);
    });

    source.addEventListener("chunk", (event) => {
      if (!this.#resultsEl) return;
      const list = this.#resultsEl.querySelector(".search-results-list");
      if (!list) return;
      const html = event.data || "";
      if (html) {
        list.insertAdjacentHTML("beforeend", html);
      }
    });

    source.addEventListener("done", (event) => {
      this.#finishSearchStream();
      this.#updateStreamStatus(event?.data);
    });

    source.addEventListener("error", () => {
      console.debug("[search] stream error", {
        readyState: source.readyState,
      });
      this.#finishSearchStream();
    });
  }

  #applyStreamInitial(html) {
    const wrap = this.#resultsEl;
    if (!wrap) return;
    wrap.innerHTML = html || "";
    this.#handleAfterSwap({ detail: { target: wrap } });
  }

  #finishSearchStream() {
    this.#spinnerController?.stop();
    if (this.#resultsEl) {
      this.#resultsEl.removeAttribute("aria-busy");
      this.#resultsEl.classList.remove("is-streaming");
    }
    this.#closeSearchStream();
  }

  #updateStreamStatus(payload) {
    if (!this.#resultsEl || !payload) return;
    let data = null;
    try {
      data = JSON.parse(payload);
    } catch {
      return;
    }
    if (!data || typeof data.showing !== "number") return;
    const showing = data.showing;
    const totalKnown = Boolean(data.total_known);
    const sr = this.#resultsEl.querySelector(
      ".search-results-status .visually-hidden",
    );
    if (!sr) return;
    const suffix = totalKnown ? "." : "+.";
    sr.textContent = `Showing ${showing} search result${showing === 1 ? "" : "s"}${suffix}`;
  }

  #closeSearchStream() {
    if (this.#streamSource) {
      this.#streamSource.close();
      this.#streamSource = null;
    }
  }

  #loadRecentSearches(force = false) {
    if (!this.autocompleteStore) return null;
    this.applyAutocompleteCandidates();
    return this.scheduleAutocompleteFetch({
      immediate: true,
      bypassCache: force,
    });
  }

  async #fetchRecentAutocompleteCandidates(signal) {
    try {
      const response = await fetch("/search/recent", {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        signal,
      });
      if (!response.ok) {
        return [];
      }

      const payload = await response.json().catch(() => null);
      if (!payload) {
        return [];
      }

      const recent = Array.isArray(payload.recent) ? payload.recent : [];
      const frecentTags = Array.isArray(payload.frecent_tags) ? payload.frecent_tags : [];
      const entries = [];
      const seen = new Set();

      for (const candidate of [...recent, ...frecentTags]) {
        if (typeof candidate !== "string") continue;
        const entry = buildSearchEntry(candidate);
        if (!entry) continue;
        const key = this.#normalizeCandidateValue(entry);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        entries.push(entry);
        if (entries.length >= RECENT_CANDIDATE_MAX) break;
      }

      return entries;
    } catch {
      return [];
    }
  }

  #addCurrentQueryToAutocomplete() {
    if (!this.#inputEl) return;
    const value = this.#inputEl.value?.trim();
    if (!value) return;
    const entry = buildSearchEntry(value);
    if (!entry) {
      return;
    }
    const normalized = this.#normalizeCandidateValue(entry);
    if (!normalized) {
      return;
    }
    this.#recentHistory.add(entry);
    this.setAutocompleteLocalEntries("local", this.#recentHistory.values());
  }

  #handleKeydown(evt) {
    if (evt.key === "Escape") {
      this.#closeResults(true);
      return;
    }

    if (!this.#resultsEl) {
      return;
    }

    const isArrowDown = evt.key === "ArrowDown";
    const isArrowUp = evt.key === "ArrowUp";
    const isEnter = evt.key === "Enter";

    if (!isArrowDown && !isArrowUp && !isEnter) {
      return;
    }

    const target = evt.target;
    if (!(target instanceof Element)) {
      return;
    }

    const resultsWrap = this.#resultsEl;
    const isInputFocused = target === this.#inputEl;
    const isInResults = resultsWrap.contains(target);

    if (!isInputFocused && !isInResults) {
      return;
    }

    const links = this.#getResultLinks();
    if (!links.length) {
      return;
    }

    const doc = this.ownerDocument ?? document;
    const activeElement = doc.activeElement;
    const activeLink =
      activeElement instanceof Element
        ? activeElement.closest("#search-results a[data-target]")
        : null;

    if (isArrowDown) {
      evt.preventDefault();

      if (isInputFocused || !activeLink) {
        this.#focusResultAt(0, links);
        return;
      }

      const currentIndex = links.indexOf(activeLink);
      if (currentIndex === -1) {
        this.#focusResultAt(0, links);
        return;
      }

      if (currentIndex < links.length - 1) {
        this.#focusResultAt(currentIndex + 1, links);
      }
      return;
    }

    if (isArrowUp) {
      evt.preventDefault();

      if (isInputFocused && links.length) {
        this.#focusResultAt(links.length - 1, links);
        return;
      }

      if (!activeLink) {
        this.#focusResultAt(links.length - 1, links);
        return;
      }

      const currentIndex = links.indexOf(activeLink);
      if (currentIndex <= 0) {
        this.#inputEl?.focus({ preventScroll: true });
        return;
      }

      this.#focusResultAt(currentIndex - 1, links);
      return;
    }

    if (isEnter && activeLink instanceof HTMLElement) {
      evt.preventDefault();
      activeLink.click();
    }
  }

  #getResultLinks() {
    if (!this.#resultsEl) return [];
    return Array.from(
      this.#resultsEl.querySelectorAll(".search-results-list a[data-target]")
    );
  }

  #focusResultAt(index, links = null) {
    const list = links ?? this.#getResultLinks();
    if (!list.length) return null;
    const clampedIndex = Math.max(0, Math.min(index, list.length - 1));
    const target = list[clampedIndex];
    target?.focus({ preventScroll: true });
    return target ?? null;
  }

  #handleDocumentClick(evt) {
    const wrap = this.#resultsEl;
    if (!wrap) return;

    const target = getEventTarget(evt);
    if (!target) return;

    const closeTrigger = target.closest('[data-action="close-search-overlay"]');
    if (closeTrigger) {
      wrap.querySelector(".sr-panel")?.classList.add("pop-exit");
      this.#closeResults(true);
      return;
    }

    const link = target.closest("#search-results a[data-target]");
    if (link) {
      this.#navigateToResult(link, evt);
      return;
    }

    const panel = wrap.querySelector(".sr-panel");
    if (
      panel &&
      !panel.contains(target) &&
      target !== this.#inputEl &&
      !target.closest(".meta-chip")
    ) {
      this.#closeResults(true);
    }
  }

  #navigateToResult(link, evt) {
    const currentId = document.getElementById("chat")?.dataset.date;
    const targetId = link.dataset.target;

    if (link.dataset.date === currentId) {
      evt.preventDefault();
      this.#closeResults(true);
      if (targetId) {
        scrollToHighlight(null, {
          targetId,
          pushHistory: true,
        });
      }
    } else {
      this.#closeResults(true, { immediate: true });
    }
  }

  #closeResults(clearInput = false, options = {}) {
    const wrap = this.#resultsEl;
    const finish = () => {
      this.#spinnerController?.stop();
      this.#closeSearchStream();
      if (this.#streamTimer) {
        window.clearTimeout(this.#streamTimer);
        this.#streamTimer = null;
      }
      if (!wrap) {
        return;
      }
      wrap.classList.remove("is-open");
      wrap.classList.remove("is-streaming");
      wrap.removeAttribute("aria-busy");
      wrap.innerHTML = "";
      this.#deactivateOverlayListeners();
    };

    if (!wrap) {
      finish();
      return;
    }


    const { immediate = false } = options;
    if (clearInput && this.#inputEl) this.#inputEl.value = "";

    const panel = wrap.querySelector(".sr-panel");

    if (!panel) {
      finish();
      return;
    }

    panel.classList.remove("pop-enter");

    const completeExit = () => {
      panel.classList.remove("pop-exit");
      finish();
    };

    if (immediate || prefersReducedMotion()) {
      completeExit();
      return;
    }

    panel.classList.add("pop-exit");
    panel.addEventListener("animationend", completeExit, { once: true });
  }

  #handlePageShow(event) {
    if (!this.isConnected) return;

    const persisted = !!event?.persisted;

    this.#refreshInputState({
      forceAutocomplete: persisted,
      forceRecent: persisted,
      reason: "pageshow",
    });

    if (persisted) {
      this.applyAutocompleteCandidates();
    }

    this.#refreshOverlayListeners();
  }

  #handlePageHide(event) {
    if (!this.isConnected) return;
    if (!event?.persisted) {
      return;
    }

    this.#deactivateOverlayListeners();

    const hasAutocomplete = !!this.autocompleteController;
    const hasListeners = !!this.#inputListeners;

    if (!hasAutocomplete && !hasListeners) {
      return;
    }

    this.destroyAutocompleteController();
    this.#inputListeners = this.disposeListenerBag(this.#inputListeners);
    this.#inputEl = null;
  }

  #handleHistoryRestore() {
    if (!this.isConnected) return;

    this.#refreshInputState({
      forceAutocomplete: true,
      forceRecent: true,
      reason: "history-restore",
    });

    this.applyAutocompleteCandidates();
    this.#refreshOverlayListeners();
  }

  #handlePopState() {
    if (!this.isConnected) return;

    this.#refreshInputState({
      forceRecent: true,
      reason: "popstate",
    });
  }

  getAutocompleteControllerOptions() {
    return {
      minLength: 1,
      emitInputEvent: false,
      prepareQuery: normalizeSearchValue,
      prepareCandidate: normalizeSearchValue,
    };
  }

  getAutocompleteInputConfig() {
    return {
      selector: "#search-input",
      observe: true,
    };
  }

  getAutocompleteStoreOptions() {
    return {
      debounceMs: 0,
      maxResults: RECENT_CANDIDATE_MAX,
      cacheTimeMs: RECENT_REFRESH_MIN_MS,
      fetchCandidates: (_, context = {}) =>
        this.#fetchRecentAutocompleteCandidates(context?.signal),
      buildCacheKey: () => "recent",
      getCandidateKey: (candidate) => this.#normalizeCandidateValue(candidate),
    };
  }

  transformAutocompleteCandidates(candidates) {
    if (!Array.isArray(candidates)) {
      return [];
    }
    return candidates.filter((candidate) => this.#normalizeCandidateValue(candidate));
  }

  buildAutocompleteFetchParams() {
    return { query: "", context: {} };
  }

  onAutocompleteCommit() {
    this.#addCurrentQueryToAutocomplete();
  }

  normalizeAutocompleteCandidate(candidate) {
    return this.#normalizeCandidateValue(candidate);
  }

  onAutocompleteInputChanged(input, previous, meta = {}) {
    const next = input instanceof HTMLInputElement ? input : null;
    this.#inputEl = next;

    this.#inputListeners = this.disposeListenerBag(this.#inputListeners);

    if (next) {
      const bag = this.createListenerBag();
      bag.add(next, "input", this.#inputHandler);
      bag.add(next, "focus", this.#focusHandler);
      this.#inputListeners = bag;
    }

    this.#registerShortcuts();

    if (meta?.reason === "mutation" && meta.initialized) {
      this.#loadRecentSearches(true);
    }
  }

  #refreshInputState(options = {}) {
    if (!this.isConnected) return;

    const { forceAutocomplete = false, forceRecent = false, reason = null } =
      options ?? {};

    const reinitialized = this.refreshAutocompleteController({
      force: forceAutocomplete,
      reason,
    });

    const input = this.autocompleteInput;
    this.#inputEl = input instanceof HTMLInputElement ? input : null;

    if (!this.#inputEl) {
      return;
    }

    if (forceRecent || reinitialized) {
      this.#loadRecentSearches(true);
    } else {
      this.#loadRecentSearches();
    }
  }

  #refreshOverlayListeners() {
    const wrap = this.#resultsEl;
    if (!wrap) return;
    if (!wrap.classList.contains("is-open")) {
      return;
    }
    const panel = wrap.querySelector(".sr-panel");
    if (!panel) {
      return;
    }
    this.#activateOverlayListeners();
  }


  #normalizeCandidateValue(entry) {
    if (!entry) return "";
    const value =
      typeof entry === "string"
        ? entry
        : typeof entry.value === "string"
          ? entry.value
          : "";
    if (!value) return "";
    return normalizeSearchValue(value).toLowerCase();
  }
}

function registerSearchOverlay() {
  if (!customElements.get("search-overlay")) {
    customElements.define("search-overlay", SearchOverlay);
  }
}

registerSearchOverlay();
document.addEventListener("app:rehydrate", registerSearchOverlay);
