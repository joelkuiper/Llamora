function collectEntries(node) {
  const entries = [];
  if (!node || node.nodeType !== Node.ELEMENT_NODE) return entries;
  if (node.classList?.contains("entry")) {
    entries.push(node);
  }
  node.querySelectorAll?.(".entry").forEach((el) => {
    entries.push(el);
  });
  return entries;
}

export function armEntryAnimations(node) {
  const entries = collectEntries(node);
  entries.forEach((entry) => {
    entry.classList.add("motion-animate-entry");
  });
}

export function armInitialEntryAnimations(entries) {
  if (!entries || entries.nodeType !== Node.ELEMENT_NODE) return;
  if (entries.dataset.animApplied === "true") return;
  entries.dataset.animApplied = "true";
  requestAnimationFrame(() => {
    armEntryAnimations(entries);
  });
}
