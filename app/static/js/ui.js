export const SPINNER = {
  interval: 80,
  frames: ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"],
};

function spin(el, text = "") {
  let i = 0;
  el.textContent = text
    ? `${SPINNER.frames[i]} ${text}`
    : SPINNER.frames[i];
  return setInterval(() => {
    i = (i + 1) % SPINNER.frames.length;
    el.textContent = text
      ? `${SPINNER.frames[i]} ${text}`
      : SPINNER.frames[i];
  }, SPINNER.interval);
}

export function startButtonSpinner(btn, loadingText = "Loading") {
  if (!btn || btn.dataset.spinning === "1") return;
  const originalText = btn.textContent;
  btn.dataset.spinning = "1";
  btn.dataset.originalText = originalText;
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.textContent = "";
  const spinnerEl = document.createElement("span");
  spinnerEl.className = "spinner";
  spinnerEl.setAttribute("aria-hidden", "true");
  btn.appendChild(spinnerEl);
  btn.append(" ", loadingText);
  const id = spin(spinnerEl);
  btn.dataset.spinnerId = String(id);
  return id;
}

export function stopButtonSpinner(btn) {
  const id = btn && btn.dataset.spinnerId;
  if (id) clearInterval(Number(id));
  if (btn && btn.dataset.originalText)
    btn.textContent = btn.dataset.originalText;
  if (btn) {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.removeAttribute("data-spinner-id");
    btn.removeAttribute("data-spinning");
  }
}

function flashHighlight(el) {
  if (!el) return;
  el.classList.remove("no-anim");
  el.classList.add("highlight");
  el.style.animation = "flash 1s ease-in-out";
  el.addEventListener(
    "animationend",
    () => {
      el.classList.remove("highlight");
      el.style.animation = "";
      el.classList.add("no-anim");
    },
    { once: true }
  );
}

class SearchUIController {
  constructor() {
    this.abortController = null;
    this.observer = null;
    this.spinnerIntervalId = null;
    this.wrap = null;
    this.input = null;

    this.handleAfterSwap = this.handleAfterSwap.bind(this);
    this.handleInput = this.handleInput.bind(this);
    this.handleKeydown = this.handleKeydown.bind(this);
    this.handleDocumentClick = this.handleDocumentClick.bind(this);
  }

  init() {
    this.wrap = document.getElementById("search-results");
    this.input = document.getElementById("search-input");
    this.setupSpinnerObserver();

    if (this.abortController) {
      return this;
    }

    this.abortController = new AbortController();
    const { signal } = this.abortController;

    if (this.input) {
      this.input.addEventListener("input", this.handleInput, { signal });
    }

    document.body.addEventListener("htmx:afterSwap", this.handleAfterSwap, {
      signal,
    });
    document.addEventListener("keydown", this.handleKeydown, { signal });
    document.addEventListener("click", this.handleDocumentClick, { signal });

    return this;
  }

  destroy() {
    this.closeResults(false, { immediate: true });

    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    if (this.spinnerIntervalId) {
      clearInterval(this.spinnerIntervalId);
      this.spinnerIntervalId = null;
    }

    this.wrap = null;
    this.input = null;
  }

  setupSpinnerObserver() {
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    if (this.spinnerIntervalId) {
      clearInterval(this.spinnerIntervalId);
      this.spinnerIntervalId = null;
    }

    const spinner = document.getElementById("search-spinner");
    if (!spinner) return;

    const update = () => {
      if (spinner.classList.contains("htmx-request")) {
        if (!this.spinnerIntervalId) {
          this.spinnerIntervalId = spin(spinner);
        }
      } else if (this.spinnerIntervalId) {
        clearInterval(this.spinnerIntervalId);
        this.spinnerIntervalId = null;
        spinner.textContent = "";
      }
    };

    update();
    this.observer = new MutationObserver(update);
    this.observer.observe(spinner, {
      attributes: true,
      attributeFilter: ["class"],
    });
  }

  handleAfterSwap(evt) {
    const wrap = this.wrap;
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

  handleInput() {
    if (!this.input || this.input.value.trim()) return;
    this.closeResults();
  }

  handleKeydown(evt) {
    if (evt.key === "Escape") {
      this.closeResults(true);
    }
  }

  handleDocumentClick(evt) {
    const wrap = this.wrap;
    if (!wrap) return;

    const target = evt.target instanceof Element ? evt.target : evt.target?.parentElement;
    if (!target) return;

    if (target.closest("#search-results .overlay-close")) {
      this.closeResults(true);
      return;
    }

    const link = target.closest("#search-results a[data-target]");
    if (link) {
      this.navigateToResult(link, evt);
      return;
    }

    const panel = wrap.querySelector(".sr-panel");
    if (
      panel &&
      !panel.contains(target) &&
      target !== this.input &&
      !target.closest(".meta-chip")
    ) {
      this.closeResults(true);
    }
  }

  navigateToResult(link, evt) {
    const currentId = document.getElementById("chat")?.dataset.date;
    const targetId = link.dataset.target;

    if (link.dataset.date === currentId) {
      evt.preventDefault();
      this.closeResults(true);
      const el = document.getElementById(targetId);
      if (el) {
        history.pushState(null, "", `${window.location.pathname}?target=${targetId}`);
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        flashHighlight(el);
      }
    } else {
      this.closeResults(true, { immediate: true });
    }
  }

  closeResults(clearInput = false, options = {}) {
    const wrap = this.wrap;
    if (!wrap) return;

    const { immediate = false } = options;
    if (clearInput && this.input) this.input.value = "";

    const panel = wrap.querySelector(".sr-panel");
    const finish = () => {
      wrap.classList.remove("is-open");
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

let searchControllerInstance = null;

export function initSearchUI() {
  if (!searchControllerInstance) {
    searchControllerInstance = new SearchUIController();
  }

  return searchControllerInstance.init();
}

export function scrollToHighlight() {
  const params = new URLSearchParams(window.location.search);
  let target = params.get("target");
  if (!target && window.location.hash.startsWith("#msg-")) {
    target = window.location.hash.substring(1);
    params.set("target", target);
  }
  if (target) {
    if (window.location.hash || params.get("target") !== target) {
      history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
    }
    const el = document.getElementById(target);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      flashHighlight(el);
    }
  }
}
