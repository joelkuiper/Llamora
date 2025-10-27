import { flashHighlight, createInlineSpinner } from "../ui.js";
import { ReactiveElement } from "../utils/reactive-element.js";

const getEventTarget = (evt) => {
  const target = evt.target;
  if (target instanceof Element) {
    return target;
  }
  return target?.parentElement ?? null;
};

export class SearchOverlay extends ReactiveElement {
  #listeners = null;
  #overlayListeners = null;
  #spinnerController = null;
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
    super.connectedCallback();
    this.#resultsEl = this.querySelector("#search-results");
    this.#inputEl = this.querySelector("#search-input");
    this.#spinnerEl = this.querySelector("#search-spinner");

    if (!this.#spinnerController) {
      this.#spinnerController = createInlineSpinner(this.#spinnerEl);
    } else {
      this.#spinnerController.setElement(this.#spinnerEl);
    }

    this.#stopSpinner();
    this.#deactivateOverlayListeners();

    this.#listeners = this.resetListenerBag(this.#listeners);
    const listeners = this.#listeners;

    if (this.#inputEl) {
      listeners.add(this.#inputEl, "input", this.#inputHandler);
    }

    this.watchHtmxRequests(this, {
      within: this,
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

    this.#stopSpinner();
    this.#spinnerController?.setElement(null);
    this.#resultsEl = null;
    this.#inputEl = null;
    this.#spinnerEl = null;
    super.disconnectedCallback();
  }

  #startSpinner() {
    this.#spinnerController?.start();
  }

  #stopSpinner() {
    this.#spinnerController?.stop();
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
