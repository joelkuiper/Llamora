import { registerHydrationOwner } from "./services/hydration-owners.js";

function updateStrength(container) {
  const meter = container.querySelector(".strength-meter");
  const score = parseInt(meter?.dataset.score || "0", 10);
  const form = container.closest("form");
  const submit = form?.querySelector('button[type="submit"]');
  if (submit) submit.disabled = score < 3;
}

function refreshAll() {
  document.querySelectorAll(".password-strength").forEach(updateStrength);
}

function refreshInContext(context = document) {
  if (context instanceof Element && context.matches(".password-strength")) {
    updateStrength(context);
    return;
  }
  const scope = context && typeof context.querySelectorAll === "function" ? context : document;
  scope.querySelectorAll(".password-strength").forEach(updateStrength);
}

document.addEventListener("DOMContentLoaded", refreshAll);
registerHydrationOwner({
  id: "password-strength",
  selector: ".password-strength",
  hydrate: (context) => {
    refreshInContext(context);
  },
});
