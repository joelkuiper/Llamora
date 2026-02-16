if (!globalThis.__appRuntime) {
  globalThis.__appRuntime = {
    imports: new Map(),
  };
}
const globalState = globalThis.__appRuntime;

function importOnce(key, loader) {
  if (!globalState.imports.has(key)) {
    globalState.imports.set(key, Promise.resolve().then(loader));
  }
  return globalState.imports.get(key);
}

function resolveScope(regionId) {
  if (!regionId || regionId === "document") {
    return document;
  }
  return document.getElementById(regionId) || document;
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
  const { init: initLifecycle } = await importOnce("lifecycle", () => import("../lifecycle.js"));
  initLifecycle();
  await Promise.all([
    importOnce("app-init", () => import("../app-init.js")),
    importOnce("logo-toggle", () => import("../logo-toggle.js")),
    importOnce("tooltip", () => import("../tooltip.js")),
    importOnce("confirm-modal", () => import("../components/confirm-modal.js")),
    importOnce("tags-view", () => import("../components/tags-view.js")),
  ]);
}

const FEATURE_IMPORTS = {
  entries: {
    selector: "entry-view",
    loader: () => import("../entries-entry.js"),
  },
  entryTags: {
    selector: "entry-tags",
    loader: () => import("../components/entry-tags.js"),
  },
  calendar: {
    selector: "calendar-control",
    loader: () => import("../components/calendar.js"),
  },
  search: {
    selector: "search-overlay",
    loader: () => import("../components/search-overlay.js"),
  },
  viewMode: {
    selector: "#view-mode-toggle",
    loader: () => import("../components/view-mode.js"),
  },
  scrollEdge: {
    selector: "scroll-edge-button",
    loader: () => import("../components/scroll-edge-button.js"),
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
    scope?.querySelector?.(selector) || document.querySelector(selector);

  Object.entries(FEATURE_IMPORTS).forEach(([key, { selector, loader }]) => {
    if (resolver(selector)) {
      loaders.push(importOnce(key, loader));
    }
  });

  return Promise.all(loaders);
}

async function processRegion(regionId = "document") {
  const scope = resolveScope(regionId);
  await ensureShell();
  await ensureFeatureModules(scope);

  globalThis.appInit?.initGlobalShell?.();
}

function onReady(fn) {
  if (document.readyState === "complete" || document.readyState === "interactive") {
    queueMicrotask(fn);
  } else {
    document.addEventListener("DOMContentLoaded", fn, { once: true });
  }
}

async function handleRehydrateEvent(event) {
  const regionId = event?.detail?.regionId || "document";
  await processRegion(regionId);
}

onReady(async () => {
  await processRegion("document");
  document.addEventListener("app:rehydrate", handleRehydrateEvent);
});
