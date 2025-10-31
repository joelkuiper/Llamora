import { flashHighlight, clearScrollTarget, createInlineSpinner } from "../ui.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import { InlineAutocompleteController } from "../utils/inline-autocomplete.js";

const getEventTarget = (evt) => {
  const target = evt.target;
  if (target instanceof Element) {
    return target;
  }
  return target?.parentElement ?? null;
};

const RECENT_REFRESH_MIN_MS = 5000;

const trimSearchValue = (value) => (typeof value === "string" ? value.trim() : "");

const collapseSearchWhitespace = (value) =>
  trimSearchValue(value).replace(/\s+/g, " ");

const prepareSearchAutocompleteValue = (value) => {
  const collapsed = collapseSearchWhitespace(value);
  return collapsed;
};

const buildSearchAutocompleteEntry = (value) => {
  const trimmed = trimSearchValue(value);
  if (!trimmed) return null;

  const tokens = new Set();
  const addToken = (token) => {
    const base = trimSearchValue(token);
    if (!base) return;
    tokens.add(base);
    const collapsed = collapseSearchWhitespace(base);
    if (collapsed) {
      tokens.add(collapsed);
    }
  };

  addToken(trimmed);
  const collapsed = collapseSearchWhitespace(trimmed);
  if (collapsed && collapsed !== trimmed) {
    addToken(collapsed);
  }

  (collapsed || trimmed)
    .split(/\s+/)
    .filter(Boolean)
    .forEach((part) => addToken(part));

  const tokenList = Array.from(tokens)
    .filter(Boolean)
    .sort((a, b) => a.length - b.length || a.localeCompare(b));
  if (!tokenList.length) {
    tokenList.push(trimmed);
  }

  return {
    value: trimmed,
    display: trimmed,
    tokens: tokenList,
  };
};

export class SearchOverlay extends ReactiveElement {
  #listeners = null;
  #overlayListeners = null;
  #resultsEl = null;
  #inputEl = null;
  #spinnerEl = null;
  #spinnerController = null;
  #autocomplete = null;
  #beforeRequestHandler;
  #afterRequestHandler;
  #afterSwapHandler;
  #inputHandler;
  #keydownHandler;
  #documentClickHandler;
  #focusHandler;
  #recentFetchPromise = null;
  #recentLoaded = false;
  #recentFetchedAt = 0;

  constructor() {
    super();
    this.#beforeRequestHandler = (event) => this.#handleBeforeRequest(event);
    this.#afterRequestHandler = (event) => this.#handleAfterRequest(event);
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#inputHandler = () => this.#handleInput();
    this.#keydownHandler = (event) => this.#handleKeydown(event);
    this.#documentClickHandler = (event) => this.#handleDocumentClick(event);
    this.#focusHandler = () => this.#handleInputFocus();
  }

  connectedCallback() {
    super.connectedCallback();
    this.#resultsEl = this.querySelector("#search-results");
    this.#inputEl = this.querySelector("#search-input");
    this.#spinnerEl = this.querySelector("#search-spinner");
    if (!this.#spinnerController) {
      this.#spinnerController = createInlineSpinner(this.#spinnerEl);
    } else {
      this.#spinnerController.setElement(this.#spinnerEl);
    }
    this.#deactivateOverlayListeners();

    this.#listeners = this.resetListenerBag(this.#listeners);
    const listeners = this.#listeners;

    if (this.#inputEl) {
      listeners.add(this.#inputEl, "input", this.#inputHandler);
      listeners.add(this.#inputEl, "focus", this.#focusHandler);
      this.#initAutocomplete();
    }

    const eventTarget = this.ownerDocument ?? document;

    this.watchHtmxRequests(eventTarget, {
      within: (event) => this.#isRelevantRequest(event),
      bag: listeners,
      onStart: this.#beforeRequestHandler,
      onEnd: this.#afterRequestHandler,
    });
    listeners.add(this, "htmx:afterSwap", this.#afterSwapHandler);
  }

  disconnectedCallback() {
    this.#closeResults(false, { immediate: true });

    this.#deactivateOverlayListeners();
    this.#listeners = this.disposeListenerBag(this.#listeners);

    this.#destroyAutocomplete();
    this.#spinnerController?.stop();
    this.#spinnerController?.setElement(null);
    this.#spinnerController = null;
    this.#resultsEl = null;
    this.#inputEl = null;
    this.#spinnerEl = null;
    super.disconnectedCallback();
  }

  #activateOverlayListeners() {
    if (this.#overlayListeners || !this.isConnected) return;

    const bag = this.resetListenerBag(this.#overlayListeners);
    const doc = this.ownerDocument ?? document;
    bag.add(doc, "keydown", this.#keydownHandler);
    bag.add(doc, "click", this.#documentClickHandler);
    this.#overlayListeners = bag;
  }

  #deactivateOverlayListeners() {
    this.#overlayListeners = this.disposeListenerBag(this.#overlayListeners);
  }

  #handleBeforeRequest(event) {
    if (!this.#isRelevantRequest(event)) return;
    const wrap = this.#resultsEl;
    if (wrap) {
      wrap.setAttribute("aria-busy", "true");
    }
    this.#spinnerController?.start();
  }

  #handleAfterRequest(event) {
    if (!this.#isRelevantRequest(event)) return;
    const wrap = this.#resultsEl;
    if (wrap) {
      wrap.removeAttribute("aria-busy");
    }
    this.#spinnerController?.stop();
  }

  #isRelevantRequest(event) {
    const source = event?.target;
    if (source instanceof Element && this.contains(source)) {
      return true;
    }

    const detailTarget = event?.detail?.target;
    const results = this.#resultsEl;

    if (results && detailTarget instanceof Element) {
      return detailTarget === results;
    }

    return false;
  }

  #handleAfterSwap(evt) {
    const wrap = this.#resultsEl;
    if (!wrap || evt.detail?.target !== wrap) return;


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

    panel.classList.add("pop-enter");
    panel.addEventListener(
      "animationend",
      () => {
        panel.classList.remove("pop-enter");
        wrap.classList.add("is-open");
        this.#activateOverlayListeners();
      },
      { once: true }
    );
    this.#addCurrentQueryToAutocomplete();
    this.#loadRecentSearches();
  }

  #handleInput() {
    if (!this.#inputEl || this.#inputEl.value.trim()) return;
    this.#closeResults();
  }

  #handleInputFocus() {
    this.#loadRecentSearches();
  }

  #initAutocomplete() {
    if (!this.#inputEl) return;
    this.#autocomplete?.destroy();
    this.#autocomplete = new InlineAutocompleteController(this.#inputEl, {
      minLength: 1,
      emitInputEvent: false,
      prepareQuery: prepareSearchAutocompleteValue,
      prepareCandidate: prepareSearchAutocompleteValue,
      onCommit: () => {
        this.#addCurrentQueryToAutocomplete();
      },
    });
  }

  #destroyAutocomplete() {
    if (this.#autocomplete) {
      this.#autocomplete.destroy();
    }
    this.#autocomplete = null;
  }

  #loadRecentSearches(force = false) {
    if (!this.#inputEl) return;
    if (this.#recentFetchPromise) {
      return this.#recentFetchPromise;
    }

    const now = Date.now();
    if (!force && this.#recentLoaded && now - this.#recentFetchedAt < RECENT_REFRESH_MIN_MS) {
      return;
    }

    const promise = fetch("/search/recent", {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then((response) => {
        if (!response.ok) {
          return null;
        }
        return response.json().catch(() => null);
      })
      .then((payload) => {
        if (!payload || !Array.isArray(payload.recent)) {
          return;
        }
        const unique = [];
        for (const value of payload.recent) {
          if (typeof value !== "string") continue;
          const trimmed = value.trim();
          if (!trimmed || unique.includes(trimmed)) continue;
          unique.push(trimmed);
        }
        const entries = unique
          .map((value) => buildSearchAutocompleteEntry(value))
          .filter(Boolean);
        if (entries.length) {
          this.#autocomplete?.setCandidates(entries);
        } else {
          this.#autocomplete?.clearCandidates();
        }
        this.#recentLoaded = true;
        this.#recentFetchedAt = Date.now();
      })
      .catch((error) => {
      })
      .finally(() => {
        this.#recentFetchPromise = null;
      });

    this.#recentFetchPromise = promise;
    return promise;
  }

  #addCurrentQueryToAutocomplete() {
    if (!this.#autocomplete || !this.#inputEl) return;
    const value = this.#inputEl.value?.trim();
    if (!value) return;
    const entry = buildSearchAutocompleteEntry(value);
    if (!entry) {
      return;
    }
    this.#autocomplete.addCandidate(entry);
  }

  #handleKeydown(evt) {
    if (evt.key === "Escape") {
      this.#closeResults(true);
    }
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
      const el = document.getElementById(targetId);
      if (el) {
        history.pushState(null, "", `${window.location.pathname}?target=${targetId}`);
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        flashHighlight(el);
        clearScrollTarget(targetId, { emitEvent: false });
      }
    } else {
      this.#closeResults(true, { immediate: true });
    }
  }

  #closeResults(clearInput = false, options = {}) {
    const wrap = this.#resultsEl;
    if (!wrap) return;


    const { immediate = false } = options;
    if (clearInput && this.#inputEl) this.#inputEl.value = "";

    const panel = wrap.querySelector(".sr-panel");
    const finish = () => {
      wrap.classList.remove("is-open");
      wrap.removeAttribute("aria-busy");
      wrap.innerHTML = "";
      this.#deactivateOverlayListeners();
    };

    if (!panel) {
      finish();
      return;
    }

    panel.classList.remove("pop-enter");

    if (immediate) {
      panel.classList.remove("pop-exit");
      finish();
      return;
    }

    panel.classList.add("pop-exit");
    panel.addEventListener("animationend", finish, { once: true });
  }
}

if (!customElements.get("search-overlay")) {
  customElements.define("search-overlay", SearchOverlay);
}
