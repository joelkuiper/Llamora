export class CacheStore {
  #backend;
  #prefix;
  #defaultTTL;

  constructor({ backend = globalThis.sessionStorage, prefix, defaultTTL = null }) {
    this.#backend = backend;
    this.#prefix = prefix;
    this.#defaultTTL = defaultTTL;
  }

  get(key) {
    try {
      const raw = this.#backend.getItem(`${this.#prefix}${key}`);
      if (!raw) return null;
      const { v, e } = JSON.parse(raw);
      if (e !== null && e <= Date.now()) {
        this.#backend.removeItem(`${this.#prefix}${key}`);
        return null;
      }
      return v ?? null;
    } catch {
      return null;
    }
  }

  set(key, value, ttlMs = this.#defaultTTL) {
    try {
      const e = ttlMs != null ? Date.now() + ttlMs : null;
      this.#backend.setItem(`${this.#prefix}${key}`, JSON.stringify({ v: value, e }));
    } catch {
      // quota or disabled storage
    }
  }

  delete(key) {
    try {
      this.#backend.removeItem(`${this.#prefix}${key}`);
    } catch {
      // storage may be disabled
    }
  }

  clear() {
    try {
      const toRemove = [];
      for (let i = 0; i < this.#backend.length; i++) {
        const k = this.#backend.key(i);
        if (k?.startsWith(this.#prefix)) {
          toRemove.push(k);
        }
      }
      for (const k of toRemove) {
        this.#backend.removeItem(k);
      }
    } catch {
      // storage may be disabled
    }
  }
}

export const draftStore = new CacheStore({
  backend: globalThis.sessionStorage,
  prefix: "llamora:draft:",
});

export const prefStore = new CacheStore({
  backend: globalThis.localStorage,
  prefix: "llamora:pref:",
});

export const sessionStore = new CacheStore({
  backend: globalThis.sessionStorage,
  prefix: "llamora:session:",
});

export function clearAllStores() {
  try {
    for (const backend of [globalThis.localStorage, globalThis.sessionStorage]) {
      const toRemove = [];
      for (let i = 0; i < backend.length; i++) {
        const k = backend.key(i);
        if (k?.startsWith("llamora:")) {
          toRemove.push(k);
        }
      }
      for (const k of toRemove) {
        backend.removeItem(k);
      }
    }
  } catch {
    // storage may be disabled
  }
}
