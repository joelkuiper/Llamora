import { prefStore } from "../utils/storage.js";

let listenerRegistered = false;

function getEntriesDay() {
  const entries = document.querySelector("#entries");
  return entries?.dataset?.date || null;
}

function readCollapsed(day) {
  if (!day) return false;
  return !!prefStore.get(`opening:${day}`);
}

function writeCollapsed(day, collapsed) {
  if (!day) return;
  if (collapsed) {
    prefStore.set(`opening:${day}`, true);
  } else {
    prefStore.delete(`opening:${day}`);
  }
}

function applyCollapsed(opening, collapsed) {
  if (!(opening instanceof Element)) return;
  opening.classList.toggle("is-collapsed", collapsed);
  const toggle = opening.querySelector("[data-opening-toggle]");
  if (toggle) {
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", collapsed ? "Expand day opening" : "Collapse day opening");
  }
}

function setDocumentCollapsed(collapsed) {
  if (collapsed) {
    document.documentElement.dataset.openingCollapsed = "1";
  } else {
    delete document.documentElement.dataset.openingCollapsed;
  }
}

function getOpeningNodes() {
  return Array.from(document.querySelectorAll(".entry--opening"));
}

function syncOpeningState(options = {}) {
  const day = getEntriesDay();
  const openings = getOpeningNodes();
  if (!openings.length || !day) {
    setDocumentCollapsed(false);
    return;
  }

  const animate = options.animate === true;
  if (!animate) {
    document.documentElement.classList.add("opening-syncing");
  }

  const collapsed = readCollapsed(day);
  openings.forEach((opening) => {
    applyCollapsed(opening, collapsed);
  });
  setDocumentCollapsed(collapsed);

  if (!animate) {
    requestAnimationFrame(() => {
      document.documentElement.classList.remove("opening-syncing");
    });
  }
}

function handleToggle(event) {
  const toggle = event.target?.closest?.("[data-opening-toggle]");
  if (!toggle) return;
  const opening = toggle.closest?.(".entry--opening");
  if (!opening) return;

  const day = getEntriesDay();
  if (!day) return;

  const nextCollapsed = !readCollapsed(day);
  writeCollapsed(day, nextCollapsed);
  applyCollapsed(opening, nextCollapsed);
  setDocumentCollapsed(nextCollapsed);
}

function registerOpeningToggle() {
  syncOpeningState();
  if (listenerRegistered) return;
  document.addEventListener("click", handleToggle);
  listenerRegistered = true;
}

registerOpeningToggle();
document.addEventListener("app:rehydrate", () => {
  syncOpeningState({ animate: false });
  if (!listenerRegistered) {
    document.addEventListener("click", handleToggle);
    listenerRegistered = true;
  }
});
