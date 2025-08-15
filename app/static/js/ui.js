export const SPINNER = {
  interval: 80,
  frames: ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"],
};

function spin(el, text = "") {
  let i = 0;
  el.textContent = text ? `${SPINNER.frames[i]} ${text}` : SPINNER.frames[i];
  return setInterval(() => {
    i = (i + 1) % SPINNER.frames.length;
    el.textContent = text ? `${SPINNER.frames[i]} ${text}` : SPINNER.frames[i];
  }, SPINNER.interval);
}

export function startButtonSpinner(btn, loadingText = "Loading") {
  if (!btn || btn.dataset.spinning === "1") return;
  const originalText = btn.textContent;
  btn.dataset.spinning = "1";
  btn.dataset.originalText = originalText;
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  const id = spin(btn, loadingText);
  btn.dataset.spinnerId = String(id);
  return id;
}

export function stopButtonSpinner(btn) {
  const id = btn && btn.dataset.spinnerId;
  if (id) clearInterval(Number(id));
  if (btn && btn.dataset.originalText) btn.textContent = btn.dataset.originalText;
  if (btn) {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.removeAttribute("data-spinner-id");
    btn.removeAttribute("data-spinning");
  }
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
  let results = document.getElementById("search-results");
  if (input && results) {
    const closeResults = () => {
      results.hidden = true;
    };

    input.addEventListener("input", () => {
      if (!input.value.trim()) {
        closeResults();
      }
    });

    input.addEventListener("focus", () => {
      if (input.value.trim()) {
        input.form.dispatchEvent(new Event("submit", { bubbles: true }));
      }
    });

    document.addEventListener("keydown", (evt) => {
      if (evt.key === "Escape") {
        closeResults();
      }
    });

    document.addEventListener("click", (evt) => {
      const target = evt.target;
      if (
        !results.hidden &&
        !results.contains(target) &&
        target !== input
      ) {
        closeResults();
      }
    });

    document.addEventListener("click", (evt) => {
      if (evt.target.closest("#search-close")) {
        closeResults();
      }
    });

    document.body.addEventListener("htmx:afterSwap", (evt) => {
      if (evt.target.id === "search-results") {
        results = document.getElementById("search-results");
        results.hidden = !results.querySelector("li");
      }
    });
  }
}

export function scrollToHighlight() {
  const params = new URLSearchParams(window.location.search);
  let target = params.get("target");
  if (!target && window.location.hash.startsWith("#msg-")) {
    target = window.location.hash.substring(1);
  }
  if (target) {
    const el = document.getElementById(target);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("highlight");
      setTimeout(() => el.classList.remove("highlight"), 2000);
    }
  }
}

