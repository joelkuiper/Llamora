const OPENING_STORAGE_PREFIX = "opening_collapsed_";
let listenerRegistered = false;

function getEntriesDay() {
  const entries = document.querySelector("#entries");
  return entries?.dataset?.date || null;
}

function getStorageKey(day) {
  return day ? `${OPENING_STORAGE_PREFIX}${day}` : null;
}

function readCollapsed(day) {
  const key = getStorageKey(day);
  if (!key) return false;
  try {
    return localStorage.getItem(key) === "1";
  } catch (error) {
    return false;
  }
}

function writeCollapsed(day, collapsed) {
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

function applyCollapsed(opening, collapsed) {
  if (!(opening instanceof Element)) return;
  opening.classList.toggle("is-collapsed", collapsed);
  const toggle = opening.querySelector("[data-opening-toggle]");
  if (toggle) {
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute(
      "aria-label",
      collapsed ? "Expand day opening" : "Collapse day opening"
    );
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

function isStreamNode(opening) {
  return opening?.classList?.contains("opening-stream");
}

function choosePrimary(openings) {
  if (!openings.length) return null;
  const nonStream = openings.find((node) => !isStreamNode(node));
  return nonStream || openings[0];
}

function dedupeOpenings() {
  const openings = getOpeningNodes();
  if (openings.length <= 1) {
    return openings[0] || null;
  }

  const primary = choosePrimary(openings);
  openings.forEach((opening) => {
    if (opening === primary) return;
    if (typeof opening.abort === "function") {
      opening.abort({ reason: "duplicate:opening" });
    }
    opening.remove();
  });
  return primary;
}

function syncOpeningState() {
  const day = getEntriesDay();
  const opening = dedupeOpenings();
  if (!opening || !day) {
    setDocumentCollapsed(false);
    return;
  }

  const collapsed = readCollapsed(day);
  applyCollapsed(opening, collapsed);
  setDocumentCollapsed(collapsed);
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
document.addEventListener("app:rehydrate", registerOpeningToggle);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    syncOpeningState();
  }
});
window.addEventListener("pageshow", () => {
  requestAnimationFrame(syncOpeningState);
});
document.body?.addEventListener("htmx:afterSwap", () => {
  syncOpeningState();
});
