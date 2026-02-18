if (!globalThis.__appRuntime) {
  globalThis.__appRuntime = {
    imports: new Map(),
    lastContext: null,
    rehydratePendingContext: null,
    rehydrateScheduled: false,
    rehydrateInFlight: false,
  };
}
const globalState = globalThis.__appRuntime;

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
  const { init: initLifecycle } = await importOnce("lifecycle", () => import("../lifecycle.js"));
  initLifecycle();
  await Promise.all([
    importOnce("app-init", () => import("../app-init.js")),
    importOnce("logo-toggle", () => import("../logo-toggle.js")),
    importOnce("tooltip", () => import("../tooltip.js")),
    importOnce("confirm-modal", () => import("../components/confirm-modal.js")),
    importOnce("tags-view", () => import("../components/tags-view/index.js")),
  ]);
}

const FEATURE_IMPORTS = {
  entries: {
    selector: "entry-view",
    loader: () => import("../components/entries-view/index.js"),
  },
  responseStream: {
    selector: "response-stream",
    loader: () => import("../components/response-stream.js"),
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
  const unresolved = Object.entries(FEATURE_IMPORTS).filter(
    ([key]) => !globalState.imports.has(key),
  );
  if (!unresolved.length) {
    return [];
  }

  const resolver = (selector) => {
    if (!selector) return null;
    if (scope?.querySelector) {
      const local = scope.querySelector(selector);
      if (local) return local;
    }
    return document.querySelector(selector);
  };

  unresolved.forEach(([key, { selector, loader }]) => {
    if (resolver(selector)) {
      loaders.push(importOnce(key, loader));
    }
  });

  return Promise.all(loaders);
}

async function processContent(context) {
  const scope = resolveScope(context);
  globalState.lastContext = scope;

  await ensureShell();
  await ensureFeatureModules(scope);

  globalThis.appInit?.initGlobalShell?.();
}

async function rehydrate(context) {
  await processContent(context);

  const lifecycle = await importOnce("lifecycle", () => import("../lifecycle.js"));
  lifecycle.rehydrate({ reason: "init", context: resolveScope(context) });
}

function scheduleProcessContent(context) {
  // Coalescing contract:
  // - `hx-on::after-settle` can fire many times during one navigation.
  // - only the latest context should be processed.
  // - at most one process pass runs per frame; any overlap is folded into
  //   the next scheduled pass to keep transitions and loading indicators smooth.
  globalState.rehydratePendingContext = resolveScope(context);
  if (globalState.rehydrateScheduled) {
    return;
  }
  globalState.rehydrateScheduled = true;

  const run = async () => {
    if (globalState.rehydrateInFlight) {
      globalState.rehydrateScheduled = false;
      if (globalState.rehydratePendingContext) {
        scheduleProcessContent(globalState.rehydratePendingContext);
      }
      return;
    }

    globalState.rehydrateScheduled = false;
    const nextContext = globalState.rehydratePendingContext || document;
    globalState.rehydratePendingContext = null;
    globalState.rehydrateInFlight = true;
    try {
      await processContent(nextContext);
    } finally {
      globalState.rehydrateInFlight = false;
      if (globalState.rehydratePendingContext) {
        scheduleProcessContent(globalState.rehydratePendingContext);
      }
    }
  };

  if (typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(() => {
      void run();
    });
  } else {
    queueMicrotask(() => {
      void run();
    });
  }
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

// hx-on::after-settle calls this for every htmx swap settle.
// Only ensure modules are loaded — don't dispatch app:rehydrate.
// Real lifecycle events (bfcache, history, major swaps, visibility)
// are handled by lifecycle.js directly.
globalThis.appRuntime.rehydrate = scheduleProcessContent;
