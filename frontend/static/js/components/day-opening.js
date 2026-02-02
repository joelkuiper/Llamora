let listenerRegistered = false;

function handleToggle(event) {
  const toggle = event.target?.closest?.("[data-opening-toggle]");
  if (!toggle) return;
  const opening = toggle.closest?.(".message--opening");
  if (!opening) return;

  const isCollapsed = opening.classList.toggle("is-collapsed");
  toggle.setAttribute("aria-expanded", String(!isCollapsed));
  toggle.setAttribute(
    "aria-label",
    isCollapsed ? "Expand day opening" : "Collapse day opening"
  );
}

function registerOpeningToggle() {
  if (listenerRegistered) return;
  document.addEventListener("click", handleToggle);
  listenerRegistered = true;
}

registerOpeningToggle();
document.addEventListener("app:rehydrate", registerOpeningToggle);
