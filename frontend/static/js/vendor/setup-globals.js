const globalScope = typeof globalThis !== "undefined" ? globalThis : window;

const pendingLoads = new Map();

function loadScript(url) {
  const href = url instanceof URL ? url.href : String(url);
  if (pendingLoads.has(href)) {
    return pendingLoads.get(href);
  }

  if (typeof document === "undefined") {
    pendingLoads.set(
      href,
      Promise.reject(new Error("No document available to load vendor scripts.")),
    );
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
  { path: "hyperlist.min.js", globals: ["HyperList"] },
  { path: "htmx.min.js", globals: ["htmx"] },
  { path: "markdown-it.min.js", globals: ["MarkdownIt"] },
  { path: "markdown-it-task-lists.min.js", globals: ["markdownitTaskLists"] },
  { path: "purify.min.js", globals: ["DOMPurify"] },
  { path: "floating-ui.min.js", globals: ["FloatingUIDOM"] },
];

for (const spec of vendorSpecs) {
  if (!hasAnyGlobal(spec.globals)) {
    const url = new URL(spec.path, vendorBase);
    await loadScript(url);
  }
}

if (globalScope.DOMPurify && globalScope.DOMPurify.default?.sanitize) {
  globalScope.DOMPurify = globalScope.DOMPurify.default;
}

const resolvedGlobals = {
  HyperList: globalScope.HyperList?.default || globalScope.HyperList,
  htmx: globalScope.htmx,
  MarkdownIt: globalScope.MarkdownIt,
  markdownitTaskLists: globalScope.markdownitTaskLists,
  DOMPurify: globalScope.DOMPurify,
  FloatingUIDOM: globalScope.FloatingUIDOM?.default || globalScope.FloatingUIDOM,
};

const requiredGlobals = [
  ["HyperList", resolvedGlobals.HyperList],
  ["htmx", resolvedGlobals.htmx],
  ["MarkdownIt", resolvedGlobals.MarkdownIt],
  ["markdownitTaskLists", resolvedGlobals.markdownitTaskLists],
  ["DOMPurify", resolvedGlobals.DOMPurify],
  ["FloatingUIDOM", resolvedGlobals.FloatingUIDOM],
];

for (const [name, value] of requiredGlobals) {
  if (!value) {
    throw new Error(`Failed to initialize vendor global: ${name}`);
  }
}

export const ready = Promise.resolve(resolvedGlobals);
export const HyperList = resolvedGlobals.HyperList;
export const htmx = resolvedGlobals.htmx;
export const MarkdownIt = resolvedGlobals.MarkdownIt;
export const markdownitTaskLists = resolvedGlobals.markdownitTaskLists;
export const DOMPurify = resolvedGlobals.DOMPurify;
export const computePosition = resolvedGlobals.FloatingUIDOM?.computePosition;
export const autoUpdate = resolvedGlobals.FloatingUIDOM?.autoUpdate;
export const offset = resolvedGlobals.FloatingUIDOM?.offset;
export const flip = resolvedGlobals.FloatingUIDOM?.flip;
export const shift = resolvedGlobals.FloatingUIDOM?.shift;
export const size = resolvedGlobals.FloatingUIDOM?.size;
export default resolvedGlobals;
