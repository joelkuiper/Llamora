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

document.addEventListener("DOMContentLoaded", refreshAll);
document.addEventListener("app:rehydrate", refreshAll);
