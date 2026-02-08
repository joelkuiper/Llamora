import { getTimezone } from "./services/datetime.js";
import { startButtonSpinner, stopButtonSpinner } from "./ui.js";
import { runWhenDocumentReady } from "./utils/dom-ready.js";

const FORM_SELECTOR = ".form-container form, .profile-modal form";

function collectForms(root = document) {
  const forms = [];
  const scope = root instanceof Document ? root : (root ?? document);

  if (!scope) {
    return forms;
  }

  if (scope instanceof Element && scope.matches(FORM_SELECTOR)) {
    forms.push(scope);
  }

  if (typeof scope.querySelectorAll === "function") {
    forms.push(...scope.querySelectorAll(FORM_SELECTOR));
  }

  return forms;
}

export function initForms(root = document) {
  getTimezone();

  const elements = collectForms(root);

  elements.forEach((form) => {
    if (form.dataset.initFormsBound === "1") return;
    form.dataset.initFormsBound = "1";

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

function registerHtmxHandlers() {
  const body = document.body;
  if (!body) return;

  const handleHtmxEvent = (event) => {
    const fragmentRoot = event.target ?? document;
    initForms(fragmentRoot);
  };

  body.addEventListener("htmx:load", handleHtmxEvent);
  body.addEventListener("htmx:afterSwap", handleHtmxEvent);
}

const onReady = () => {
  initForms(document);
  registerHtmxHandlers();
};

function resetSpinningButtons(scope = document) {
  if (!scope) return;

  const forms = collectForms(scope);

  forms.forEach((form) => {
    form.querySelectorAll('button[data-spinning="1"]').forEach((btn) => {
      stopButtonSpinner(btn);
    });
  });
}

runWhenDocumentReady(onReady);

if (typeof window !== "undefined") {
  window.appInit = window.appInit || {};
  window.appInit.initForms = initForms;

  window.addEventListener("pageshow", (event) => {
    // When navigating back to a cached page, make sure any submit buttons are reset.
    const scope = event.target instanceof Document ? event.target : document;
    resetSpinningButtons(scope);
  });
}
