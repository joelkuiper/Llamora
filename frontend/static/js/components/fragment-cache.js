import { fragmentCache } from "../utils/storage.js";

const resolveTarget = (event) => event?.detail?.target;

document.body.addEventListener("htmx:beforeRequest", (event) => {
  const target = resolveTarget(event);
  if (!(target instanceof HTMLElement)) return;
  const cacheKey = target.dataset.cacheKey;
  if (!cacheKey) return;
  const cached = fragmentCache.get(cacheKey);
  if (!cached) return;
  event.preventDefault();
  target.innerHTML = cached;
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  const target = resolveTarget(event);
  if (!(target instanceof HTMLElement)) return;
  const cacheKey = target.dataset.cacheKey;
  if (!cacheKey) return;
  const ttl = Number.parseInt(target.dataset.cacheTtl || "", 10);
  const ttlMs = Number.isNaN(ttl) ? undefined : ttl * 1000;
  fragmentCache.set(cacheKey, target.innerHTML, ttlMs);
});

globalThis.llamoraCache = fragmentCache;
