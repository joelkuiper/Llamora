import { normalizeAutocompleteValue } from "./autocomplete-normalize.js";

const DEFAULT_DEBOUNCE_MS = 200;

const defaultBuildCacheKey = (query, context = {}) => {
  if (context && typeof context.cacheKey === "string") {
    return context.cacheKey;
  }
  const normalizedQuery = typeof query === "string" ? query.trim().toLowerCase() : "";
  try {
    return JSON.stringify([normalizedQuery, context ?? {}]);
  } catch {
    return normalizedQuery;
  }
};

const defaultGetCandidateKey = (candidate) => normalizeAutocompleteValue(candidate);

const defaultMergeCandidates = (remoteCandidates, localCollections, helpers) => {
  const merged = [];
  const seen = new Set();

  const addList = (list) => {
    if (!Array.isArray(list)) {
      return;
    }
    for (const candidate of list) {
      if (candidate == null) continue;
      const key = helpers.getCandidateKey(candidate, helpers.context);
      if (!key) continue;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(candidate);
    }
  };

  addList(remoteCandidates);
  for (const locals of localCollections) {
    addList(locals);
  }

  return merged;
};

const toPositiveInteger = (value, fallback = null) => {
  const parsed = Number.parseInt(value, 10);
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }
  return fallback;
};

const sanitizeList = (entries) => {
  if (!Array.isArray(entries)) {
    return [];
  }
  return entries.filter((entry) => entry != null);
};

export class AutocompleteDataStore {
  #fetchCandidates;
  #debounceMs;
  #buildCacheKey;
  #getCandidateKey;
  #mergeCandidates;
  #maxResults;
  #cacheTimeMs;
  #cache;
  #timer;
  #pendingKey;
  #pendingPromise;
  #controller;
  #inFlightKey;
  #inFlightPromise;
  #remoteCandidates;
  #localEntries;
  #listeners;
  #currentContext;
  #mergedCandidates;
  #mergedDirty;
  #onError;

  constructor(options = {}) {
    const {
      fetchCandidates,
      debounceMs = DEFAULT_DEBOUNCE_MS,
      buildCacheKey = defaultBuildCacheKey,
      getCandidateKey = defaultGetCandidateKey,
      mergeCandidates = defaultMergeCandidates,
      maxResults = null,
      cacheTimeMs = null,
      onError = null,
    } = options;

    if (typeof fetchCandidates !== "function") {
      throw new TypeError("AutocompleteDataStore requires a fetchCandidates function");
    }

    this.#fetchCandidates = fetchCandidates;
    this.#debounceMs = Math.max(0, Number(debounceMs) || 0);
    this.#buildCacheKey = typeof buildCacheKey === "function" ? buildCacheKey : defaultBuildCacheKey;
    this.#getCandidateKey = typeof getCandidateKey === "function" ? getCandidateKey : defaultGetCandidateKey;
    this.#mergeCandidates = typeof mergeCandidates === "function" ? mergeCandidates : defaultMergeCandidates;
    this.#maxResults = toPositiveInteger(maxResults, null);
    this.#cacheTimeMs = toPositiveInteger(cacheTimeMs, null);
    this.#cache = new Map();
    this.#timer = null;
    this.#pendingKey = null;
    this.#pendingPromise = null;
    this.#controller = null;
    this.#inFlightKey = null;
    this.#inFlightPromise = null;
    this.#remoteCandidates = [];
    this.#localEntries = new Map();
    this.#listeners = new Set();
    this.#currentContext = {};
    this.#mergedCandidates = [];
    this.#mergedDirty = true;
    this.#onError = typeof onError === "function" ? onError : null;
  }

  scheduleFetch(query, context = {}, options = {}) {
    const { immediate = false, bypassCache = false } = options;
    const ctx = context ?? {};
    const key = this.#buildCacheKey(query, ctx);

    if (!immediate && this.#pendingKey === key && this.#timer) {
      return this.#pendingPromise ?? null;
    }

    if (this.#inFlightKey === key && this.#inFlightPromise) {
      return this.#inFlightPromise;
    }

    if (bypassCache) {
      this.#cache.delete(key);
    }

    const execute = () => {
      this.#pendingKey = null;
      const request = this.#executeFetch(query, ctx, key, { bypassCache });
      return request;
    };

    if (immediate || this.#debounceMs <= 0) {
      this.#clearTimer();
      const request = execute();
      return request;
    }

    this.#clearTimer();
    this.#pendingKey = key;
    const pending = new Promise((resolve, reject) => {
      this.#timer = globalThis.setTimeout(() => {
        this.#timer = null;
        execute().then(resolve, reject);
      }, this.#debounceMs);
    });
    this.#pendingPromise = pending;
    pending.finally(() => {
      if (this.#pendingPromise === pending) {
        this.#pendingPromise = null;
      }
    });
    return pending;
  }

  setLocalEntries(sourceId, entries) {
    const key = typeof sourceId === "string" ? sourceId : `${sourceId ?? "default"}`;
    const list = sanitizeList(entries);
    if (!list.length) {
      if (this.#localEntries.has(key)) {
        this.#localEntries.delete(key);
        this.#markDirty();
        this.#notify();
      }
      return;
    }
    this.#localEntries.set(key, list.slice());
    this.#markDirty();
    this.#notify();
  }

  clearLocalEntries(sourceId = null) {
    if (sourceId == null) {
      if (this.#localEntries.size === 0) {
        return;
      }
      this.#localEntries.clear();
    } else {
      const key = typeof sourceId === "string" ? sourceId : `${sourceId}`;
      if (!this.#localEntries.has(key)) {
        return;
      }
      this.#localEntries.delete(key);
    }
    this.#markDirty();
    this.#notify();
  }

  getCandidates() {
    return this.#composeCandidates().slice();
  }

  subscribe(listener, options = {}) {
    if (typeof listener !== "function") {
      return () => {};
    }
    const { immediate = true } = options;
    this.#listeners.add(listener);
    if (immediate) {
      listener(this.getCandidates());
    }
    return () => {
      this.#listeners.delete(listener);
    };
  }

  clearCache() {
    if (this.#cache.size === 0) {
      return;
    }
    this.#cache.clear();
  }

  reset(options = {}) {
    const { clearCache = false, clearLocal = true } = options;
    this.cancel();
    if (clearCache) {
      this.#cache.clear();
    }
    if (clearLocal) {
      this.#localEntries.clear();
    }
    this.#remoteCandidates = [];
    this.#markDirty();
    this.#notify();
  }

  cancel() {
    this.#clearTimer();
    this.#pendingKey = null;
    this.#pendingPromise = null;
    this.#cancelInFlight();
  }

  destroy() {
    this.reset({ clearCache: true, clearLocal: true });
    this.#listeners.clear();
  }

  #executeFetch(query, context, cacheKey, options = {}) {
    const { bypassCache = false } = options;

    if (!bypassCache) {
      const cached = this.#getValidCacheEntry(cacheKey);
      if (cached) {
        this.#setRemoteCandidates(cached.candidates, { key: cacheKey, context });
        return Promise.resolve(cached.candidates.slice());
      }
    }

    this.#cancelInFlight();

    const controller = new AbortController();
    this.#controller = controller;
    const fetchContext = { ...context, signal: controller.signal, cacheKey };
    this.#inFlightKey = cacheKey;

    const request = Promise.resolve()
      .then(() => this.#fetchCandidates(query, fetchContext))
      .then((result) => {
        if (controller.signal.aborted) {
          return [];
        }
        const candidates = sanitizeList(result);
        this.#cache.set(cacheKey, { candidates, timestamp: Date.now() });
        if (this.#inFlightKey === cacheKey) {
          this.#setRemoteCandidates(candidates, { key: cacheKey, context });
        }
        return candidates.slice();
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return [];
        }
        this.#cache.delete(cacheKey);
        if (this.#inFlightKey === cacheKey) {
          this.#setRemoteCandidates([], { key: cacheKey, context });
        }
        if (this.#onError) {
          try {
            this.#onError(error, { key: cacheKey, query, context });
          } catch {
            // Ignore listener errors.
          }
        }
        return [];
      })
      .finally(() => {
        if (this.#controller === controller) {
          this.#controller = null;
        }
        if (this.#inFlightPromise === request) {
          this.#inFlightPromise = null;
          this.#inFlightKey = null;
        }
      });

    this.#inFlightPromise = request;
    return request;
  }

  #setRemoteCandidates(candidates, details) {
    this.#remoteCandidates = sanitizeList(candidates);
    this.#currentContext = details?.context ?? {};
    this.#markDirty();
    this.#notify();
  }

  #notify() {
    if (!this.#listeners.size) {
      return;
    }
    const merged = this.#composeCandidates();
    for (const listener of this.#listeners) {
      try {
        listener(merged.slice());
      } catch {
        // Listener errors are ignored to avoid breaking updates.
      }
    }
  }

  #composeCandidates() {
    if (!this.#mergedDirty) {
      return this.#mergedCandidates.slice();
    }

    const remote = this.#remoteCandidates.slice();
    const locals = Array.from(this.#localEntries.values(), (list) => list.slice());
    const merged = this.#mergeCandidates(remote, locals, {
      context: this.#currentContext,
      getCandidateKey: (candidate) => this.#getCandidateKey(candidate, this.#currentContext),
    });

    const prepared = sanitizeList(Array.isArray(merged) ? merged : []);
    const limited = this.#maxResults ? prepared.slice(0, this.#maxResults) : prepared;
    this.#mergedCandidates = limited;
    this.#mergedDirty = false;
    return this.#mergedCandidates.slice();
  }

  #getValidCacheEntry(key) {
    const cached = this.#cache.get(key);
    if (!cached) {
      return null;
    }
    if (!this.#cacheTimeMs) {
      return cached;
    }
    const age = Date.now() - cached.timestamp;
    if (age <= this.#cacheTimeMs) {
      return cached;
    }
    this.#cache.delete(key);
    return null;
  }

  #cancelInFlight() {
    if (this.#controller) {
      this.#controller.abort();
      this.#controller = null;
    }
    this.#inFlightKey = null;
    this.#inFlightPromise = null;
  }

  #clearTimer() {
    if (this.#timer != null) {
      globalThis.clearTimeout(this.#timer);
      this.#timer = null;
    }
    this.#pendingPromise = null;
    this.#pendingKey = null;
  }

  #markDirty() {
    this.#mergedDirty = true;
  }
}

