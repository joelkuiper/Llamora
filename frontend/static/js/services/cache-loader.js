import { invalidateCache } from "./invalidation-bus.js";
import { getValue, setValue } from "./lockbox-store.js";

const inflight = new Map();

const truthy = (value) => {
  const normalized = String(value || "")
    .trim()
    .toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
};

const resolveConfig = (el, overrides = {}) => {
  const dataset = el?.dataset || {};
  const namespace = String(overrides.namespace ?? dataset.cacheNamespace ?? "").trim();
  const key = String(overrides.key ?? dataset.cacheKey ?? "").trim();
  const digest = String(overrides.digest ?? dataset.cacheDigest ?? "").trim();
  const triggerEvent = String(overrides.triggerEvent ?? dataset.cacheTrigger ?? "").trim();
  const kind = String(overrides.kind ?? dataset.cacheKind ?? "").trim();
  const stripHtmx = overrides.stripHtmx ?? truthy(dataset.cacheStripHtmx);
  return {
    namespace,
    key,
    digest,
    triggerEvent,
    kind,
    stripHtmx,
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

const applyCached = (el, html, stripHtmx) => {
  if (!(el instanceof HTMLElement)) return;
  el.innerHTML = html;
  if (!stripHtmx) return;
  el.removeAttribute("hx-get");
  el.removeAttribute("hx-trigger");
  el.removeAttribute("hx-swap");
  el.removeAttribute("hx-disinherit");
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
    const { namespace, key, digest, triggerEvent, kind, stripHtmx } = resolveConfig(el, overrides);
    if (!namespace || !key) return false;
    const requestKey = cacheKeyFor(namespace, key);
    let fetchPromise = inflight.get(requestKey);
    if (!fetchPromise) {
      fetchPromise = getValue(namespace, key);
      inflight.set(requestKey, fetchPromise);
      fetchPromise.finally(() => inflight.delete(requestKey));
    }
    const payload = await fetchPromise;
    const resolved = resolveCachedValue({
      payload,
      digest,
      kind,
      namespace,
      key,
    });
    const cached = resolved.value;
    if (cached) {
      applyCached(el, cached, stripHtmx);
      return true;
    }
    if (!triggerEvent) return false;
    if (el.dataset?.cacheRequested === "1") return false;
    el.dataset.cacheRequested = "1";
    if (typeof htmx !== "undefined") {
      window.setTimeout(() => {
        htmx.trigger(el, triggerEvent);
      }, 60);
    }
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
