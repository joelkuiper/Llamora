const globalState = (globalThis.__appRuntime ??= {
  imports: new Map(),
  lastContext: null,
});

function importOnce(key, loader) {
  if (!globalState.imports.has(key)) {
    globalState.imports.set(key, Promise.resolve().then(loader));
  }
  return globalState.imports.get(key);
}

function resolveScope(context) {
  if (!context || context === document || context === document.body) {
    return document;
  }
  if (context instanceof Event) {
    const detail = context.detail || {};
    return detail.target || detail.elt || document;
  }
  if (context instanceof Element || context instanceof DocumentFragment) {
    return context;
  }
  return document;
}

async function ensureVendors() {
  const vendorModule = await importOnce("vendors", () => import("../vendor/setup-globals.js"));
  const htmx = vendorModule?.htmx ?? globalThis.htmx;

  if (htmx) {
    await importOnce("htmx-ext-sse", async () => {
      if (htmx.findExtension?.("sse") || htmx.extensions?.sse) return null;
      return import("../vendor/htmx-ext-sse.js");
    });

    await importOnce("htmx-ext-response-targets", async () => {
      if (htmx.findExtension?.("response-targets") || htmx.extensions?.["response-targets"]) {
        return null;
      }
      return import("../vendor/htmx-ext-response-targets.js");
    });
  }

  return vendorModule;
}

async function ensureShell() {
  await ensureVendors();
  await Promise.all([
    importOnce("app-init", () => import("../app-init.js")),
    importOnce("logo-toggle", () => import("../logo-toggle.js")),
    importOnce("tooltip", () => import("../tooltip.js")),
    importOnce("confirm-modal", () => import("../components/confirm-modal.js")),
  ]);
}

const FEATURE_IMPORTS = {
  entries: {
    selector: "entry-view",
    loader: () => import("../entries-entry.js"),
  },
  calendar: {
    selector: "calendar-control",
    loader: () => import("../components/calendar.js"),
  },
  search: {
    selector: "search-overlay",
    loader: () => import("../components/search-overlay.js"),
  },
  profile: {
    selector: "[data-profile-modal]",
    loader: () =>
      Promise.all([
        import("../forms.js"),
        import("../password-strength.js"),
        import("../components/profile-modal.js"),
      ]),
  },
};

async function ensureFeatureModules(scope) {
  const loaders = [];
  const resolver = (selector) =>
    (scope && scope.querySelector && scope.querySelector(selector)) ||
    document.querySelector(selector);

  Object.entries(FEATURE_IMPORTS).forEach(([key, { selector, loader }]) => {
    if (resolver(selector)) {
      loaders.push(importOnce(key, loader));
    }
  });

  return Promise.all(loaders);
}

async function rehydrate(context) {
  const scope = resolveScope(context);
  globalState.lastContext = scope;

  await ensureShell();
  await ensureFeatureModules(scope);

  document.dispatchEvent(new CustomEvent("app:rehydrate", { detail: { context: scope } }));

  globalThis.appInit?.initGlobalShell?.();
}

function onReady(fn) {
  if (document.readyState === "complete" || document.readyState === "interactive") {
    queueMicrotask(fn);
  } else {
    document.addEventListener("DOMContentLoaded", fn, { once: true });
  }
}

onReady(() => rehydrate(document));

if (!globalThis.appRuntime) {
  globalThis.appRuntime = {};
}

globalThis.appRuntime.rehydrate = rehydrate;
