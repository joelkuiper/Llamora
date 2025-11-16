const globalScope = typeof globalThis !== "undefined" ? globalThis : window;

const pendingLoads = new Map();

function loadScript(url) {
  const href = url instanceof URL ? url.href : String(url);
  if (pendingLoads.has(href)) {
    return pendingLoads.get(href);
  }

  if (typeof document === "undefined") {
    pendingLoads.set(href, Promise.reject(new Error("No document available to load vendor scripts.")));
    return pendingLoads.get(href);
  }

  const target = document.head || document.body || document.documentElement;
  const promise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = href;
    script.async = false;
    script.defer = false;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load vendor script: ${href}`));
    target.append(script);
  });

  pendingLoads.set(href, promise);
  return promise;
}

function hasAnyGlobal(names) {
  return names.some((name) => globalScope[name] !== undefined && globalScope[name] !== null);
}

const vendorBase = (() => {
  if (typeof location !== "undefined") {
    return new URL("/static/js/vendor/", location.origin);
  }
  return new URL("./", import.meta.url);
})();

const vendorSpecs = [
  { path: "htmx.min.js", globals: ["htmx"] },
  { path: "marked.umd.js", globals: ["marked"] },
  { path: "purify.min.js", globals: ["DOMPurify"] },
  { path: "popper.min.jsm.js", globals: ["createPopper", "Popper"] },
];

for (const spec of vendorSpecs) {
  if (!hasAnyGlobal(spec.globals)) {
    const url = new URL(spec.path, vendorBase);
    await loadScript(url);
  }
}

if (!globalScope.createPopper && globalScope.Popper?.createPopper) {
  globalScope.createPopper = globalScope.Popper.createPopper;
}

if (globalScope.createPopper) {
  if (!globalScope.Popper || typeof globalScope.Popper !== "object") {
    globalScope.Popper = { createPopper: globalScope.createPopper };
  } else if (!globalScope.Popper.createPopper) {
    globalScope.Popper.createPopper = globalScope.createPopper;
  }
}

const resolvedGlobals = {
  htmx: globalScope.htmx,
  marked: globalScope.marked,
  DOMPurify: globalScope.DOMPurify,
  createPopper: globalScope.createPopper,
};

const requiredGlobals = [
  ["htmx", resolvedGlobals.htmx],
  ["marked", resolvedGlobals.marked],
  ["DOMPurify", resolvedGlobals.DOMPurify],
];

for (const [name, value] of requiredGlobals) {
  if (!value) {
    throw new Error(`Failed to initialize vendor global: ${name}`);
  }
}

export const ready = Promise.resolve(resolvedGlobals);
export const htmx = resolvedGlobals.htmx;
export const marked = resolvedGlobals.marked;
export const DOMPurify = resolvedGlobals.DOMPurify;
export const createPopper = resolvedGlobals.createPopper;

export default resolvedGlobals;
