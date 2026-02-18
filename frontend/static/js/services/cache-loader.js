import { invalidateCache } from "./invalidation-bus.js";
import { getValue, setValue } from "./lockbox-store.js";

const inflight = new Map();
const CACHE_FRESH_WINDOW_MS = 1500;
let requestGateRegistered = false;

const parseTimestamp = (value) => {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? parsed : null;
};

const clearCacheAppliedMark = (el) => {
  if (!(el instanceof HTMLElement)) return;
  delete el.dataset.cacheAppliedAt;
  delete el.dataset.cacheAppliedNamespace;
  delete el.dataset.cacheAppliedKey;
  delete el.dataset.cacheAppliedDigest;
};

const markCacheApplied = (el, { namespace, key, digest }) => {
  if (!(el instanceof HTMLElement)) return;
  el.dataset.cacheAppliedAt = String(Date.now());
  el.dataset.cacheAppliedNamespace = String(namespace || "").trim();
  el.dataset.cacheAppliedKey = String(key || "").trim();
  el.dataset.cacheAppliedDigest = String(digest || "").trim();
};

const isFreshCacheApplied = (el, { namespace, key, digest }) => {
  if (!(el instanceof HTMLElement)) return false;
  const appliedAt = parseTimestamp(el.dataset.cacheAppliedAt);
  if (appliedAt == null) return false;
  if (Date.now() - appliedAt > CACHE_FRESH_WINDOW_MS) {
    clearCacheAppliedMark(el);
    return false;
  }
  if (String(el.dataset.cacheAppliedNamespace || "") !== String(namespace || "")) return false;
  if (String(el.dataset.cacheAppliedKey || "") !== String(key || "")) return false;
  const appliedDigest = String(el.dataset.cacheAppliedDigest || "");
  const activeDigest = String(digest || "");
  if (appliedDigest && activeDigest && appliedDigest !== activeDigest) return false;
  return true;
};

const markHydrating = (el, active) => {
  if (!(el instanceof HTMLElement)) return;
  if (active) {
    el.dataset.cacheHydrating = "1";
    return;
  }
  delete el.dataset.cacheHydrating;
};

const markRequestQueued = (el) => {
  if (!(el instanceof HTMLElement)) return;
  el.dataset.cacheRequested = "1";
};

const clearRequestQueued = (el) => {
  if (!(el instanceof HTMLElement)) return;
  delete el.dataset.cacheRequested;
};

const resolveConfig = (el, overrides = {}) => {
  const dataset = el?.dataset || {};
  const namespace = String(overrides.namespace ?? dataset.cacheNamespace ?? "").trim();
  const key = String(overrides.key ?? dataset.cacheKey ?? "").trim();
  const digest = String(overrides.digest ?? dataset.cacheDigest ?? "").trim();
  const triggerEvent = String(overrides.triggerEvent ?? dataset.cacheTrigger ?? "").trim();
  const kind = String(overrides.kind ?? dataset.cacheKind ?? "").trim();
  return {
    namespace,
    key,
    digest,
    triggerEvent,
    kind,
  };
};

const cacheKeyFor = (namespace, key) => `${namespace}\u0000${key}`;

const resolveCachedValue = ({ payload, digest, kind, namespace, key }) => {
  if (!payload) return { value: "", mismatched: false };
  if (typeof payload === "string") return { value: payload, mismatched: false };
  if (typeof payload !== "object") return { value: "", mismatched: false };
  if (digest && String(payload.digest || "") !== String(digest)) {
    if (namespace && key) {
      void invalidateCache({
        namespace,
        key,
        reason: "digest-mismatch",
      });
    }
    return { value: "", mismatched: true };
  }
  if (kind === "text") {
    return { value: typeof payload.text === "string" ? payload.text : "", mismatched: false };
  }
  return { value: typeof payload.html === "string" ? payload.html : "", mismatched: false };
};

const isCacheableSummary = (el, html) => {
  if (!html) return false;
  if (el?.querySelector?.(".tag-detail-skeleton")) return false;
  if (html.includes("Summary unavailable")) return false;
  return true;
};

const shouldCache = (el, html, kind) => {
  if (kind === "summary") {
    return isCacheableSummary(el, html);
  }
  return Boolean(html);
};

const serializeValue = (value, digest, kind) => {
  if (kind === "summary") {
    return { digest: String(digest || ""), html: value };
  }
  if (kind === "text") {
    return { digest: String(digest || ""), text: value };
  }
  return value;
};

const applyCached = (el, html) => {
  if (!(el instanceof HTMLElement)) return;
  el.innerHTML = html;
};

const ensureRequestGate = () => {
  if (requestGateRegistered || typeof document === "undefined") return;
  const bind = () => {
    if (requestGateRegistered || !document.body) return;
    document.body.addEventListener("htmx:configRequest", (event) => {
      const source = event.target;
      if (!(source instanceof HTMLElement)) return;
      const { namespace, key, digest } = resolveConfig(source);
      if (!namespace || !key) return;
      if (source.dataset.cacheHydrating === "1") {
        event.preventDefault();
        return;
      }
      if (isFreshCacheApplied(source, { namespace, key, digest })) {
        event.preventDefault();
      }
    });
    const releaseQueueMark = (event) => {
      const source = event?.detail?.requestConfig?.elt || event?.detail?.elt || event?.target;
      if (!(source instanceof HTMLElement)) return;
      clearRequestQueued(source);
    };
    document.body.addEventListener("htmx:afterRequest", releaseQueueMark);
    document.body.addEventListener("htmx:responseError", releaseQueueMark);
    document.body.addEventListener("htmx:sendError", releaseQueueMark);
    document.body.addEventListener("htmx:sendAbort", releaseQueueMark);
    requestGateRegistered = true;
  };
  if (document.body) {
    bind();
  } else {
    document.addEventListener("DOMContentLoaded", bind, { once: true });
  }
};

ensureRequestGate();

const triggerLoadEvent = (el, triggerEvent) => {
  if (typeof htmx === "undefined") return;
  if (!(el instanceof HTMLElement) || !el.isConnected) return;

  let fired = false;
  const fire = () => {
    if (fired) return;
    fired = true;
    if (!el.isConnected) return;
    htmx.trigger(el, triggerEvent);
  };

  const onProcessed = (event) => {
    if (event.target !== el) return;
    document.body?.removeEventListener("htmx:afterProcessNode", onProcessed, true);
    fire();
  };

  document.body?.addEventListener("htmx:afterProcessNode", onProcessed, true);
  htmx.process(el);
  queueMicrotask(() => {
    document.body?.removeEventListener("htmx:afterProcessNode", onProcessed, true);
    fire();
  });
};

export const cacheLoader = {
  async read({ namespace, key, digest, kind } = {}) {
    const ns = String(namespace || "").trim();
    const k = String(key || "").trim();
    if (!ns || !k) return "";
    const payload = await getValue(ns, k);
    const resolved = resolveCachedValue({
      payload,
      digest,
      kind,
      namespace: ns,
      key: k,
    });
    return resolved.value;
  },

  async write({ namespace, key, digest, kind, value } = {}) {
    const ns = String(namespace || "").trim();
    const k = String(key || "").trim();
    if (!ns || !k) return false;
    if (value == null) return false;
    const payload = serializeValue(value, digest, kind);
    await setValue(ns, k, payload);
    return true;
  },

  async hydrate(el, overrides = {}) {
    if (!(el instanceof HTMLElement)) return false;
    ensureRequestGate();
    const { namespace, key, digest, triggerEvent, kind } = resolveConfig(el, overrides);
    if (!namespace || !key) return false;
    const requestKey = cacheKeyFor(namespace, key);
    let fetchPromise = inflight.get(requestKey);
    if (!fetchPromise) {
      fetchPromise = getValue(namespace, key);
      inflight.set(requestKey, fetchPromise);
      fetchPromise.finally(() => inflight.delete(requestKey));
    }
    markHydrating(el, true);
    let resolved;
    try {
      const payload = await fetchPromise;
      resolved = resolveCachedValue({
        payload,
        digest,
        kind,
        namespace,
        key,
      });
    } finally {
      markHydrating(el, false);
    }
    const cached = resolved.value;
    if (cached) {
      applyCached(el, cached);
      markCacheApplied(el, { namespace, key, digest });
      clearRequestQueued(el);
      return true;
    }
    clearCacheAppliedMark(el);
    if (!triggerEvent) return false;
    if (el.dataset?.cacheRequested === "1") return false;
    markRequestQueued(el);
    triggerLoadEvent(el, triggerEvent);
    return false;
  },

  async capture(el, overrides = {}) {
    if (!(el instanceof HTMLElement)) return false;
    const { namespace, key, digest, kind } = resolveConfig(el, overrides);
    if (!namespace || !key) return false;
    const html = el.innerHTML?.trim();
    if (!shouldCache(el, html, kind)) return false;
    const payload = serializeValue(html, digest, kind);
    await setValue(namespace, key, payload);
    return true;
  },
};

export default cacheLoader;
