import { createListenerBag } from "../utils/events.js";
import { nextModalZ } from "../utils/modal-stack.js";

const MODAL_SELECTOR = "[data-profile-modal]";
const CLOSE_SELECTOR = "[data-profile-close]";
const TAB_SELECTOR = ".profile-modal__tab";
const PROFILE_BUTTON_ID = "profile-btn";

function setProfileButtonActive(isActive) {
  const button = document.getElementById(PROFILE_BUTTON_ID);
  if (!button) return;
  button.classList.toggle("active", isActive);
}

function setActiveTab(modal, targetTab) {
  const tabs = modal.querySelectorAll(TAB_SELECTOR);
  tabs.forEach((tab) => {
    const isActive = tab.dataset.profileTab === targetTab;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
  });
}

function initProfileModal(modal) {
  if (!modal || modal.dataset.profileInit === "true") return;
  modal.dataset.profileInit = "true";

  document.body.classList.add("modal-open");
  modal.style.zIndex = String(nextModalZ());
  setProfileButtonActive(true);

  const listeners = createListenerBag();
  modal._profileListeners = listeners;

  const close = () => {
    if (!modal.isConnected) return;
    modal.classList.add("is-closing");
    window.setTimeout(() => {
      if (!modal.isConnected) return;
      listeners.abort();
      modal.remove();
      document.body.classList.remove("modal-open");
      setProfileButtonActive(false);
    }, 160);
  };

  listeners.add(modal, "click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.matches(CLOSE_SELECTOR) || target.closest(CLOSE_SELECTOR)) {
      event.preventDefault();
      close();
    }
  });

  listeners.add(document, "keydown", (event) => {
    if (event.key === "Escape") {
      close();
    }
  });

  const activeTab = modal.dataset.activeTab;
  if (activeTab) {
    setActiveTab(modal, activeTab);
  }

  const tabs = modal.querySelectorAll(TAB_SELECTOR);
  tabs.forEach((tab) => {
    listeners.add(tab, "click", () => {
      const targetTab = tab.dataset.profileTab;
      if (targetTab) {
        setActiveTab(modal, targetTab);
        modal.dataset.activeTab = targetTab;
      }
    });
  });

  const panel = modal.querySelector(".profile-modal__panel");
  if (panel && typeof panel.focus === "function") {
    panel.setAttribute("tabindex", "-1");
    panel.focus({ preventScroll: true });
  }
}

function boot(context = document) {
  const scope =
    context instanceof Element || context instanceof DocumentFragment
      ? context
      : document;
  const modal = scope.querySelector(MODAL_SELECTOR);
  if (modal) {
    initProfileModal(modal);
  } else {
    setProfileButtonActive(false);
  }
}

if (typeof document !== "undefined") {
  document.addEventListener("htmx:afterSwap", (event) => {
    boot(event.target);
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => boot(document), { once: true });
  } else {
    boot(document);
  }
}

export { initProfileModal };
