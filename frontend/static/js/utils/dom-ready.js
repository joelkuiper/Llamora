export function runWhenDocumentReady(callback) {
  if (typeof callback !== "function") {
    return;
  }

  if (typeof document === "undefined") {
    callback();
    return;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", callback, { once: true });
    return;
  }

  callback();
}
