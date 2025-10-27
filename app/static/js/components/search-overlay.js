import { flashHighlight, SPINNER } from "../ui.js";

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
  #listenerController = null;
  #observer = null;
  #spinnerIntervalId = null;
  #spinnerFrame = 0;
  #resultsEl = null;
  #inputEl = null;
  #spinnerEl = null;
  #afterSwapHandler;
  #inputHandler;
  #keydownHandler;
  #documentClickHandler;

  constructor() {
    super();
    this.#afterSwapHandler = (event) => this.#handleAfterSwap(event);
    this.#inputHandler = () => this.#handleInput();
    this.#keydownHandler = (event) => this.#handleKeydown(event);
    this.#documentClickHandler = (event) => this.#handleDocumentClick(event);
  }

  connectedCallback() {
    this.#resultsEl = this.querySelector("#search-results");
    this.#inputEl = this.querySelector("#search-input");
    this.#spinnerEl = this.querySelector("#search-spinner");

    this.#setupSpinnerObserver();

    if (this.#listenerController) {
      this.#listenerController.abort();
    }

    this.#listenerController = new AbortController();
    const { signal } = this.#listenerController;

    if (this.#inputEl) {
      this.#inputEl.addEventListener("input", this.#inputHandler, { signal });
    }

    document.body.addEventListener("htmx:afterSwap", this.#afterSwapHandler, {
      signal,
    });
    document.addEventListener("keydown", this.#keydownHandler, { signal });
    document.addEventListener("click", this.#documentClickHandler, { signal });
  }

  disconnectedCallback() {
    this.#closeResults(false, { immediate: true });

    if (this.#listenerController) {
      this.#listenerController.abort();
      this.#listenerController = null;
    }

    if (this.#observer) {
      this.#observer.disconnect();
      this.#observer = null;
    }

    if (this.#spinnerIntervalId !== null) {
      clearInterval(this.#spinnerIntervalId);
      this.#spinnerIntervalId = null;
    }

    this.#spinnerFrame = 0;
    if (this.#spinnerEl) {
      this.#spinnerEl.textContent = "";
    }
    this.#resultsEl = null;
    this.#inputEl = null;
    this.#spinnerEl = null;
  }

  #setupSpinnerObserver() {
    if (this.#observer) {
      this.#observer.disconnect();
      this.#observer = null;
    }

    if (this.#spinnerIntervalId !== null) {
      clearInterval(this.#spinnerIntervalId);
      this.#spinnerIntervalId = null;
    }

    const spinner = this.#spinnerEl;
    if (!spinner) return;

    const update = () => {
      if (spinner.classList.contains("htmx-request")) {
        if (this.#spinnerIntervalId === null) {
          this.#spinnerFrame = 0;
          spinner.textContent = SPINNER_FRAMES[this.#spinnerFrame];
          this.#spinnerIntervalId = window.setInterval(() => {
            this.#spinnerFrame = (this.#spinnerFrame + 1) % SPINNER_FRAMES.length;
            spinner.textContent = SPINNER_FRAMES[this.#spinnerFrame];
          }, SPINNER_INTERVAL);
        }
      } else if (this.#spinnerIntervalId !== null) {
        clearInterval(this.#spinnerIntervalId);
        this.#spinnerIntervalId = null;
        this.#spinnerFrame = 0;
        spinner.textContent = "";
      }
    };

    update();
    this.#observer = new MutationObserver(update);
    this.#observer.observe(spinner, {
      attributes: true,
      attributeFilter: ["class"],
    });
  }

  #handleAfterSwap(evt) {
    const wrap = this.#resultsEl;
    if (!wrap || evt.detail?.target !== wrap) return;

    const panel = wrap.querySelector(".sr-panel");
    if (!panel) {
      wrap.classList.remove("is-open");
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
