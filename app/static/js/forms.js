import { startButtonSpinner, stopButtonSpinner } from "./ui.js";
import { setTimezoneCookie } from "./timezone.js";

function initForms() {
  setTimezoneCookie();
  document.querySelectorAll(".form-container form, #profile-page form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      const btn = form.querySelector('button[type="submit"]');
      if (!btn || btn.dataset.spinning === "1") return;

      const loadingText = btn.dataset.loading || "Loading";
      startButtonSpinner(btn, loadingText);

      if (form.hasAttribute("data-download")) {
        e.preventDefault(); // prevent navigation

        try {
          const response = await fetch(form.action, { credentials: "same-origin" });
          if (!response.ok) throw new Error("Download failed");

          const blob = await response.blob();
          const disposition = response.headers.get("Content-Disposition") || "";
          const match = disposition.match(/filename="?([^";]+)"?/);
          const filename = match ? match[1] : "download";

          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        } catch (err) {
          console.error(err);
        } finally {
          stopButtonSpinner(btn);
        }
      }

      // fallback if something hangs
      setTimeout(() => {
        if (btn.dataset.spinning === "1") stopButtonSpinner(btn);
      }, 10000);
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initForms);
} else {
  initForms();
}
