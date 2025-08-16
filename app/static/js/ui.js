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
  let visible = false;

  const getResultsEl = () => (results = document.getElementById("search-results"));

  if (input && results) {
    const cleanupAfterClose = () => {
      const el = getResultsEl();
      if (!el) return;
      el.innerHTML = "";
      el.classList.remove("search-results-overlay", "sr-hide", "sr-pop");
      visible = false;
    };

    const closeResults = (clearInput = false) => {
      if (clearInput) input.value = "";

      const el = getResultsEl();          // <-- rebind to the live element
      if (!el || el.classList.contains("sr-hide")) return;

      el.classList.add("sr-hide");
      el.addEventListener("animationend", () => {
        cleanupAfterClose();
      }, { once: true });

      // safety: if animation is interrupted
      setTimeout(() => {
        const live = getResultsEl();
        if (live && live.classList.contains("sr-hide")) cleanupAfterClose();
      }, 240);
    };

    input.addEventListener("input", () => {
      if (!input.value.trim()) closeResults();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeResults(true);
    });
    document.addEventListener("click", (e) => {
      const el = getResultsEl();
      if (el.classList.contains("search-results-overlay") &&
          !el.contains(e.target) && e.target !== input) {
        closeResults(true);
      }
    });

    document.addEventListener("click", (evt) => {
      if (evt.target.closest("#search-close")) {
        closeResults(true);
      }
    });

    document.addEventListener("click", (evt) => {
      const link = evt.target.closest("#search-results a[data-target]");
      if (!link) return;

      const currentId = document.getElementById("chat")?.dataset.sessionId;
      const targetId = link.dataset.target;

      if (link.dataset.sessionId === currentId) {
        evt.preventDefault();
        closeResults(true);
        const el = document.getElementById(targetId);
        if (el) {
          history.pushState(
            null,
            "",
            `${window.location.pathname}?target=${targetId}#${targetId}`
          );
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("highlight");
          setTimeout(() => el.classList.remove("highlight"), 2000);
        }
      } else {
        closeResults(true);
      }
    });

    // Rebind after every HTMX swap so `results` stays current
    document.body.addEventListener("htmx:afterSwap", (evt) => {
      const t = evt.detail?.target;
      if (t && t.id === "search-results") {
        results = t; // <-- keep reference fresh
        const hasItems = !!results.querySelector("li");
        if (hasItems) {
          results.classList.add("search-results-overlay");
          if (!visible) {
            results.classList.add("sr-pop");
            results.addEventListener(
              "animationend",
              () => results.classList.remove("sr-pop"),
              { once: true }
            );
          }
          visible = true;
        } else {
          results.classList.remove("search-results-overlay");
          visible = false;
        }
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
