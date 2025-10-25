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

export function initSearchUI() {
  const spinner = document.getElementById("search-spinner");
  if (spinner) {
    let id = null;
    const update = () => {
      if (spinner.classList.contains("htmx-request")) {
        if (!id) id = spin(spinner);
      } else if (id) {
        clearInterval(id);
        id = null;
        spinner.textContent = "";
      }
    };
    const observer = new MutationObserver(update);
    observer.observe(spinner, { attributes: true, attributeFilter: ["class"] });
  }


  const input = document.getElementById("search-input");
  const wrap  = document.getElementById("search-results");

  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.detail?.target !== wrap) return;
    const panel = wrap.querySelector(".sr-panel");
    if (!panel) { wrap.classList.remove("is-open"); return; }

    if (wrap.classList.contains("is-open")) {
      // Already open: show immediately, no entry animation
      panel.classList.remove("htmx-added"); // avoid hidden state while typing
      return;
    }

    // First open: run the pop-in under a stable class
    panel.classList.add("pop-enter");
    panel.addEventListener("animationend", () => {
      panel.classList.remove("pop-enter");
      wrap.classList.add("is-open");
    }, { once: true });
  });

  const closeResults = (clearInput = false, options = {}) => {
    const { immediate = false } = options;
    if (clearInput && input) input.value = "";
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
  };

  // Triggers
  if (input) {
    input.addEventListener("input", () => {
      if (!input.value.trim()) closeResults();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeResults(true);
  });
  document.addEventListener("click", (e) => {
    const panel = wrap.querySelector(".sr-panel");
    if (
      panel &&
      !panel.contains(e.target) &&
      e.target !== input &&
      !e.target.closest(".meta-chip")
    ) {
      closeResults(true);
    }
  });
  document.addEventListener("click", (evt) => {
    if (evt.target.closest("#search-results .overlay-close")) closeResults(true);
  });

  document.addEventListener("click", (evt) => {
    const link = evt.target.closest("#search-results a[data-target]");
    if (!link) return;
    const currentId = document.getElementById("chat")?.dataset.date;
    const targetId  = link.dataset.target;

    if (link.dataset.date === currentId) {
      evt.preventDefault();
      closeResults(true);
      const el = document.getElementById(targetId);
      if (el) {
        history.pushState(null, "", `${window.location.pathname}?target=${targetId}`);
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        flashHighlight(el);
      }
    } else {
      closeResults(true, { immediate: true });
    }
  });
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
