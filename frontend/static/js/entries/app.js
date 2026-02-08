import "../vendor/setup-globals.js";
import "../vendor/htmx-extensions.js";

const ENTRY_LOADERS = {
  "app-shell": () => import("../runtime/loader.js"),
  "auth-forms": () => Promise.all([import("../forms.js"), import("../password-strength.js")]),
  recovery: () => import("../recovery.js"),
};

const loadedEntries = new Map();

function parseEntries(value) {
  if (!value) {
    return [];
  }
  return Array.from(
    new Set(
      value
        .split(/\s+/)
        .map((token) => token.trim())
        .filter(Boolean),
    ),
  );
}

function loadEntry(name) {
  const loader = ENTRY_LOADERS[name];
  if (!loader) {
    return null;
  }
  if (!loadedEntries.has(name)) {
    const loadPromise = Promise.resolve()
      .then(loader)
      .catch((error) => {
        console.error(`Failed to load entry module: ${name}`, error);
        loadedEntries.delete(name);
        throw error;
      });
    loadedEntries.set(name, loadPromise);
  }
  return loadedEntries.get(name);
}

function loadEntries(entries) {
  const tokens = Array.isArray(entries) ? entries : parseEntries(entries);
  const promises = tokens.map((token) => loadEntry(token)).filter((promise) => Boolean(promise));
  return Promise.all(promises);
}

function readBodyEntries() {
  const body = document.body;
  if (!body) {
    return [];
  }
  return parseEntries(body.dataset.entry || "");
}

function observeBodyEntry() {
  const body = document.body;
  if (!body || typeof MutationObserver === "undefined") {
    return;
  }

  let previous = new Set(readBodyEntries());
  const observer = new MutationObserver(() => {
    const currentEntries = new Set(readBodyEntries());
    const newEntries = Array.from(currentEntries).filter((entry) => !previous.has(entry));
    previous = currentEntries;
    if (newEntries.length > 0) {
      loadEntries(newEntries);
    }
  });

  observer.observe(body, { attributes: true, attributeFilter: ["data-entry"] });
}

function init() {
  loadEntries(readBodyEntries());
  observeBodyEntry();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}

globalThis.appEntrypoints = {
  load: loadEntries,
  parse: parseEntries,
};

export { loadEntries };
