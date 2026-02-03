const DEFAULTS = {
  title: "Confirm",
  message: "Are you sure?",
  confirmLabel: "Confirm",
  cancelLabel: "Cancel",
};

function resolveTrigger(event) {
  const detail = event?.detail ?? {};
  const candidate = detail.elt || detail.target || event.target;
  if (!candidate || !(candidate instanceof Element)) {
    return null;
  }
  return (
    candidate.closest(
      "[hx-confirm], [data-confirm-title], [data-confirm-confirm], [data-confirm-cancel], [data-confirm-variant]",
    ) || candidate
  );
}

function getConfig(trigger, question) {
  const dataset = trigger?.dataset ?? {};
  return {
    title: dataset.confirmTitle || DEFAULTS.title,
    message: dataset.confirmMessage || question || DEFAULTS.message,
    confirmLabel: dataset.confirmConfirm || DEFAULTS.confirmLabel,
    cancelLabel: dataset.confirmCancel || DEFAULTS.cancelLabel,
    variant: dataset.confirmVariant || "default",
  };
}

export function initConfirmModal(options = {}) {
  const modal = document.getElementById("confirm-modal");
  if (!modal || modal.dataset.confirmInit === "true") {
    if (!modal || !options.force) {
      return;
    }
  }

  const titleEl = modal.querySelector("#confirm-modal-title");
  const messageEl = modal.querySelector("#confirm-modal-message");
  const confirmBtn = modal.querySelector("[data-confirm-action='confirm']");
  const cancelBtn = modal.querySelector("[data-confirm-action='cancel']");
  if (!titleEl || !messageEl || !confirmBtn || !cancelBtn) {
    return;
  }

  const state = (globalThis.__confirmModalState ??= { bound: false });
  if (options.force) {
    state.bound = false;
  }
  let activeRequest = state.activeRequest || null;
  let lastFocused = state.lastFocused || null;
  let closeTimer = null;

  const openModal = (config, requestCallback) => {
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    activeRequest = requestCallback;
    titleEl.textContent = config.title;
    messageEl.textContent = config.message;
    confirmBtn.textContent = config.confirmLabel;
    cancelBtn.textContent = config.cancelLabel;
    modal.dataset.confirmVariant = config.variant;
    if (modal.parentElement !== document.body) {
      document.body.appendChild(modal);
    }
    modal.hidden = false;
    modal.removeAttribute("hidden");
    modal.setAttribute("aria-hidden", "false");
    lastFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    requestAnimationFrame(() => {
      modal.classList.add("is-open");
      confirmBtn.focus();
    });
  };

  const closeModal = (confirmed) => {
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    modal.removeAttribute("data-confirm-variant");
    const callback = activeRequest;
    activeRequest = null;
    if (closeTimer) {
      clearTimeout(closeTimer);
    }
    closeTimer = setTimeout(() => {
      modal.hidden = true;
      closeTimer = null;
    }, 200);
    if (confirmed && typeof callback === "function") {
      callback();
    }
    if (lastFocused) {
      lastFocused.focus();
    }
  };

  if (!state.bound) {
    if (state.controller) {
      state.controller.abort();
    }
    state.controller = new AbortController();
    const { signal } = state.controller;

    modal.addEventListener("click", (event) => {
      const action = event.target?.closest?.("[data-confirm-action]");
      if (!action) {
        return;
      }
      const value = action.getAttribute("data-confirm-action");
      closeModal(value === "confirm");
    }, { signal });

    document.addEventListener("keydown", (event) => {
      if (!modal.classList.contains("is-open")) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeModal(false);
      }
    }, { signal });

    document.body.addEventListener("htmx:confirm", (event) => {
      if (!modal) {
        return;
      }
      const detail = event.detail || {};
      const trigger = resolveTrigger(event);
      const hasConfirmData =
        Boolean(detail.question) ||
        Boolean(trigger?.getAttribute?.("hx-confirm")) ||
        Boolean(trigger?.dataset?.confirmTitle) ||
        Boolean(trigger?.dataset?.confirmMessage) ||
        Boolean(trigger?.dataset?.confirmConfirm) ||
        Boolean(trigger?.dataset?.confirmCancel) ||
        Boolean(trigger?.dataset?.confirmVariant);
      if (!hasConfirmData) {
        return;
      }
      const config = getConfig(trigger, detail.question);
      event.preventDefault();
      openModal(config, () => detail.issueRequest(true));
    }, { signal });

    state.bound = true;
  }

  modal.dataset.confirmInit = "true";
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initConfirmModal, { once: true });
} else {
  initConfirmModal();
}

window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    initConfirmModal({ force: true });
  }
});
