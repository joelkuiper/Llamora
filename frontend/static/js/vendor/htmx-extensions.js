import { ready as vendorReady } from "./setup-globals.js";

const globalScope = typeof globalThis !== "undefined" ? globalThis : window;
const pendingLoads = new Map();

function loadScript(url) {
  const href = url instanceof URL ? url.href : String(url);
  if (pendingLoads.has(href)) {
    return pendingLoads.get(href);
  }

  if (typeof document === "undefined") {
    pendingLoads.set(href, Promise.reject(new Error("No document available to load htmx extensions.")));
    return pendingLoads.get(href);
  }

  const target = document.head || document.body || document.documentElement;
  const promise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = href;
    script.async = false;
    script.defer = false;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load htmx extension: ${href}`));
    target.append(script);
  });

  pendingLoads.set(href, promise);
  return promise;
}

await vendorReady;

for (const extensionPath of [
  "./vendor/htmx-ext-sse.js",
  "./vendor/htmx-ext-response-targets.js",
]) {
  const url = new URL(extensionPath, import.meta.url);
  await loadScript(url);
}

export const htmx = globalScope.htmx;
export default globalScope.htmx;
