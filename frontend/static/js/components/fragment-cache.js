const DEFAULT_TTL = 6 * 60 * 60;
const STORAGE_PREFIX = "llamora-cache:";

const nowSeconds = () => Math.floor(Date.now() / 1000);

const safeParse = (raw) => {
  try {
    return JSON.parse(raw);
  } catch (error) {
    return null;
  }
};

const storage = {
  get(key) {
    if (!key) return null;
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${key}`);
    if (!raw) return null;
    const data = safeParse(raw);
    if (!data || typeof data !== "object") return null;
    if (data.expiresAt && data.expiresAt <= nowSeconds()) {
      localStorage.removeItem(`${STORAGE_PREFIX}${key}`);
      return null;
    }
    return data.value ?? null;
  },
  set(key, value, ttlSeconds = DEFAULT_TTL) {
    if (!key) return;
    const expiresAt = nowSeconds() + Math.max(30, ttlSeconds);
    const payload = JSON.stringify({ value, expiresAt });
    localStorage.setItem(`${STORAGE_PREFIX}${key}`, payload);
  },
};

const resolveTarget = (event) => event?.detail?.target;

document.body.addEventListener("htmx:beforeRequest", (event) => {
  const target = resolveTarget(event);
  if (!(target instanceof HTMLElement)) return;
  const cacheKey = target.dataset.cacheKey;
  if (!cacheKey) return;
  const cached = storage.get(cacheKey);
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
  const ttlSeconds = Number.isNaN(ttl) ? DEFAULT_TTL : ttl;
  storage.set(cacheKey, target.innerHTML, ttlSeconds);
});

globalThis.llamoraCache = storage;
