let listenerRegistered = false;
const OPENING_STORAGE_PREFIX = "opening_collapsed_";

function getOpeningDate(opening) {
  const entries = opening?.closest?.("#entries");
  return entries?.dataset?.date || null;
}

function getStorageKey(day) {
  return day ? `${OPENING_STORAGE_PREFIX}${day}` : null;
}

function setCollapsedStorage(day, collapsed) {
  const key = getStorageKey(day);
  if (!key) return;
  try {
    if (collapsed) {
      localStorage.setItem(key, "1");
    } else {
      localStorage.removeItem(key);
    }
  } catch (error) {
    // ignore storage failures
  }
}

function getCollapsedStorage(day) {
  const key = getStorageKey(day);
  if (!key) return false;
  try {
    return localStorage.getItem(key) === "1";
  } catch (error) {
    return false;
  }
}

function syncInitialState() {
  document.querySelectorAll(".entry--opening").forEach((opening) => {
    const day = getOpeningDate(opening);
    if (!getCollapsedStorage(day)) {
      return;
    }
    opening.classList.add("is-collapsed");
    const toggle = opening.querySelector("[data-opening-toggle]");
    if (toggle) {
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Expand day opening");
    }
  });
}

function handleToggle(event) {
  const toggle = event.target?.closest?.("[data-opening-toggle]");
  if (!toggle) return;
  const opening = toggle.closest?.(".entry--opening");
  if (!opening) return;

  const isCollapsed = opening.classList.toggle("is-collapsed");
  toggle.setAttribute("aria-expanded", String(!isCollapsed));
  toggle.setAttribute(
    "aria-label",
    isCollapsed ? "Expand day opening" : "Collapse day opening"
  );
  const day = getOpeningDate(opening);
  setCollapsedStorage(day, isCollapsed);
  if (isCollapsed) {
    document.documentElement.dataset.openingCollapsed = "1";
  } else {
    delete document.documentElement.dataset.openingCollapsed;
  }
}

function registerOpeningToggle() {
  if (listenerRegistered) return;
  syncInitialState();
  document.addEventListener("click", handleToggle);
  listenerRegistered = true;
}

registerOpeningToggle();
document.addEventListener("app:rehydrate", registerOpeningToggle);
