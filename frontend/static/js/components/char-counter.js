const COUNTER_SELECTOR = "[data-char-counter]";

function parseThreshold(counter) {
  const raw = counter.getAttribute("data-char-threshold");
  const value = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(value) ? value : 40;
}

function resolveTarget(counter) {
  const targetId = counter.getAttribute("data-char-counter-for");
  if (targetId) {
    const target = document.getElementById(targetId);
    if (target instanceof HTMLTextAreaElement) {
      return target;
    }
  }
  const scope = counter.closest("form") || counter.closest("entry-form");
  if (!scope) return null;
  const candidate = scope.querySelector("textarea[maxlength]");
  return candidate instanceof HTMLTextAreaElement ? candidate : null;
}

function updateCounter(counter, target) {
  const max = Number.parseInt(target.getAttribute("maxlength") || "", 10);
  if (!Number.isFinite(max)) {
    counter.textContent = "";
    counter.classList.remove("is-visible");
    return;
  }
  const remaining = max - target.value.length;
  const threshold = parseThreshold(counter);
  counter.textContent = `${remaining} left`;
  counter.classList.toggle("is-visible", remaining <= threshold);
  counter.classList.toggle("is-limit", remaining <= 0);
}

function refreshCounter(counter) {
  const target = resolveTarget(counter);
  if (!target) return;
  updateCounter(counter, target);
}

function refreshCounters(root = document) {
  root
    .querySelectorAll(COUNTER_SELECTOR)
    .forEach((counter) => refreshCounter(counter));
}

document.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLTextAreaElement)) return;
  const id = target.id;
  if (id) {
    document
      .querySelectorAll(`${COUNTER_SELECTOR}[data-char-counter-for="${id}"]`)
      .forEach((counter) => updateCounter(counter, target));
    return;
  }
  const counters = target
    .closest("form")
    ?.querySelectorAll(COUNTER_SELECTOR);
  counters?.forEach((counter) => updateCounter(counter, target));
});

document.addEventListener("reset", (event) => {
  const form = event.target;
  if (form instanceof HTMLFormElement) {
    refreshCounters(form);
  }
});

document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target instanceof Element) {
    refreshCounters(event.target);
  }
});

document.addEventListener("app:rehydrate", () => refreshCounters());

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => refreshCounters(), {
    once: true,
  });
} else {
  refreshCounters();
}
