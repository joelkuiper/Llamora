let listenerRegistered = false;

function getOpeningDate(opening) {
  const entries = opening?.closest?.("#entries");
  return entries?.dataset?.date || null;
}

function setCollapsedCookie(day, collapsed) {
  if (!day) return;
  const maxAge = 60 * 60 * 24 * 365;
  if (!collapsed) {
    document.cookie = `opening_collapsed_${day}=; Path=/; Max-Age=0`;
    return;
  }
  document.cookie = `opening_collapsed_${day}=1; Path=/; Max-Age=${maxAge}`;
}

function syncInitialState() {
  document.querySelectorAll(".entry--opening").forEach((opening) => {
    if (!opening.classList.contains("is-collapsed")) {
      return;
    }
    const toggle = opening.querySelector("[data-opening-toggle]");
    if (toggle) {
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Expand day opening");
    }
    setCollapsedCookie(getOpeningDate(opening), true);
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

  setCollapsedCookie(getOpeningDate(opening), isCollapsed);
}

function registerOpeningToggle() {
  if (listenerRegistered) return;
  syncInitialState();
  document.addEventListener("click", handleToggle);
  listenerRegistered = true;
}

registerOpeningToggle();
document.addEventListener("app:rehydrate", registerOpeningToggle);
