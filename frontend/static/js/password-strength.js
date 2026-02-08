function updateStrength(container) {
  const meter = container.querySelector(".strength-meter");
  const score = parseInt(meter?.dataset.score || "0", 10);
  const form = container.closest("form");
  const submit = form?.querySelector('button[type="submit"]');
  if (submit) submit.disabled = score < 3;
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".password-strength").forEach(updateStrength);
});

document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target.classList?.contains("password-strength")) {
    updateStrength(e.target);
  }
});
