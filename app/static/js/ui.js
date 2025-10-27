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

export function flashHighlight(el) {
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

export function scrollToHighlight(fallbackTarget) {
  const params = new URLSearchParams(window.location.search);
  let target = params.get("target");
  let consumedFallback = false;
  let shouldUpdateHistory = false;

  if (!target && window.location.hash.startsWith("#msg-")) {
    target = window.location.hash.substring(1);
    params.set("target", target);
    shouldUpdateHistory = true;
  }

  if (!target && fallbackTarget) {
    target = fallbackTarget;
    params.set("target", target);
    shouldUpdateHistory = true;
    consumedFallback = true;
  }

  if (target) {
    if (shouldUpdateHistory || window.location.hash) {
      const query = params.toString();
      const newUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
      history.replaceState(null, "", newUrl);
    }
    const el = document.getElementById(target);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      flashHighlight(el);
    }
  }

  if (consumedFallback) {
    const chatView = document.querySelector("chat-view");
    if (chatView?.dataset.scrollTarget === fallbackTarget) {
      delete chatView.dataset.scrollTarget;
    }
  }
}
