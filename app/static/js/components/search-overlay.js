import { flashHighlight, SPINNER } from "../ui.js";
import { createListenerBag } from "../utils/events.js";

const SPINNER_FRAMES = SPINNER.frames;
const SPINNER_INTERVAL = SPINNER.interval;

const getEventTarget = (evt) => {
  const target = evt.target;
  if (target instanceof Element) {
    return target;
  }
  return target?.parentElement ?? null;
};

export class SearchOverlay extends HTMLElement {
  #listeners = null;
  #overlayListeners = null;
  #spinnerIntervalId = null;
  #spinnerFrame = 0;
  #resultsEl = null;
  #inputEl = null;
  #spinnerEl = null;
  #beforeRequestHandler;
  #afterRequestHandler;
  #afterSwapHandler;
  #inputHandler;
  #keydownHandler;
  #documentClickHandler;

  constructor() {
    super();
    this.#beforeRequestHandler = (event) => this.#handleBeforeRequest(event);
    this.#afterRequestHandler = (event) => this.#handleAfterRequest(event);
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#inputHandler = () => this.#handleInput();
    this.#keydownHandler = (event) => this.#handleKeydown(event);
    this.#documentClickHandler = (event) => this.#handleDocumentClick(event);
  }

  connectedCallback() {
    this.#resultsEl = this.querySelector("#search-results");
    this.#inputEl = this.querySelector("#search-input");
    this.#spinnerEl = this.querySelector("#search-spinner");

    this.#stopSpinner();
    this.#deactivateOverlayListeners();

    if (this.#listeners) {
      this.#listeners.abort();
    }

    this.#listeners = createListenerBag();
    const listeners = this.#listeners;

    if (this.#inputEl) {
      listeners.add(this.#inputEl, "input", this.#inputHandler);
    }

    listeners.add(this, "htmx:beforeRequest", this.#beforeRequestHandler);
    listeners.add(this, "htmx:afterRequest", this.#afterRequestHandler);
    listeners.add(this, "htmx:sendError", this.#afterRequestHandler);
    listeners.add(this, "htmx:responseError", this.#afterRequestHandler);
    listeners.add(this, "htmx:afterSwap", this.#afterSwapHandler);
  }

  disconnectedCallback() {
    this.#closeResults(false, { immediate: true });

    this.#deactivateOverlayListeners();
    if (this.#listeners) {
      this.#listeners.abort();
      this.#listeners = null;
    }

    this.#stopSpinner();

    this.#spinnerFrame = 0;
    if (this.#spinnerEl) {
      this.#spinnerEl.textContent = "";
    }
    this.#resultsEl = null;
    this.#inputEl = null;
    this.#spinnerEl = null;
  }

  #startSpinner() {
    const spinner = this.#spinnerEl;
    if (!spinner || this.#spinnerIntervalId !== null) return;

    this.#spinnerFrame = 0;
    spinner.textContent = SPINNER_FRAMES[this.#spinnerFrame];
    this.#spinnerIntervalId = window.setInterval(() => {
      this.#spinnerFrame = (this.#spinnerFrame + 1) % SPINNER_FRAMES.length;
      spinner.textContent = SPINNER_FRAMES[this.#spinnerFrame];
    }, SPINNER_INTERVAL);
  }

  #stopSpinner() {
    if (this.#spinnerIntervalId !== null) {
      clearInterval(this.#spinnerIntervalId);
      this.#spinnerIntervalId = null;
    }

    if (this.#spinnerEl) {
      this.#spinnerFrame = 0;
      this.#spinnerEl.textContent = "";
    }
  }

  #activateOverlayListeners() {
    if (this.#overlayListeners || !this.isConnected) return;

    const bag = createListenerBag();
    const doc = this.ownerDocument ?? document;
    bag.add(doc, "keydown", this.#keydownHandler);
    bag.add(doc, "click", this.#documentClickHandler);
    this.#overlayListeners = bag;
  }

  #deactivateOverlayListeners() {
    if (this.#overlayListeners) {
      this.#overlayListeners.abort();
      this.#overlayListeners = null;
    }
  }

  #handleBeforeRequest(event) {
    const source = event.target;
    if (!(source instanceof Element) || !this.contains(source)) return;

    const wrap = this.#resultsEl;
    if (wrap) {
      wrap.setAttribute("aria-busy", "true");
    }
    this.#startSpinner();
  }

  #handleAfterRequest(event) {
    const source = event.target;
    if (!(source instanceof Element) || !this.contains(source)) return;

    const wrap = this.#resultsEl;
    if (wrap) {
      wrap.removeAttribute("aria-busy");
    }
    this.#stopSpinner();
  }

  #handleAfterSwap(evt) {
    const wrap = this.#resultsEl;
    if (!wrap || evt.detail?.target !== wrap) return;

    const panel = wrap.querySelector(".sr-panel");
    if (!panel) {
      wrap.classList.remove("is-open");
      this.#deactivateOverlayListeners();
      return;
    }

    if (wrap.classList.contains("is-open")) {
      panel.classList.remove("htmx-added");
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
  }

  #handleInput() {
    if (!this.#inputEl || this.#inputEl.value.trim()) return;
    this.#closeResults();
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

    if (target.closest("#search-results .overlay-close")) {
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
      this.#stopSpinner();
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
