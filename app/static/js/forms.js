import { startButtonSpinner, stopButtonSpinner } from "./ui.js";

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".form-container form").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector('button[type="submit"]');
      if (!btn || btn.dataset.spinning === "1") return;
      const loadingText = btn.dataset.loading || "Loading";
      startButtonSpinner(btn, loadingText);

      // Fallback to stop the spinner if navigation fails
      setTimeout(() => {
        if (btn.dataset.spinning === "1") stopButtonSpinner(btn);
      }, 10000);
    });
  });
});


