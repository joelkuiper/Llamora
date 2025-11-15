const defaultPrepare = (value) => value;

const defaultNormalize = (value) => {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim().toLowerCase();
  }
  if (typeof value === "object") {
    if (typeof value.value === "string") {
      return value.value.trim().toLowerCase();
    }
    if (typeof value.key === "string") {
      return value.key.trim().toLowerCase();
    }
    if (typeof value.id === "string") {
      return value.id.trim().toLowerCase();
    }
    if (typeof value.label === "string") {
      return value.label.trim().toLowerCase();
    }
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
};

const toPositiveInteger = (value, fallback = null) => {
  const parsed = Number.parseInt(value, 10);
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }
  return fallback;
};

export class AutocompleteHistory {
  #entries;
  #maxEntries;
  #normalize;
  #prepare;

  constructor(options = {}) {
    const {
      maxEntries = 20,
      normalize = defaultNormalize,
      prepare = defaultPrepare,
      initialEntries = [],
    } = options ?? {};

    this.#normalize = typeof normalize === "function" ? normalize : defaultNormalize;
    this.#prepare = typeof prepare === "function" ? prepare : defaultPrepare;

    const limit = toPositiveInteger(maxEntries, null);
    this.#maxEntries = limit ?? null;
    this.#entries = [];

    this.replace(initialEntries);
  }

  get size() {
    return this.#entries.length;
  }

  values() {
    return this.#entries.slice();
  }

  clear() {
    if (!this.#entries.length) {
      return this.values();
    }
    this.#entries = [];
    return this.values();
  }

  add(value) {
    const prepared = this.#prepareValue(value);
    if (prepared == null) {
      return this.values();
    }
    const key = this.#normalizeValue(prepared);
    if (!key) {
      return this.values();
    }
    const entries = this.#entries.filter(
      (entry) => this.#normalizeValue(entry) !== key,
    );
    entries.unshift(prepared);
    this.#entries = this.#applyLimit(entries);
    return this.values();
  }

  addMany(values) {
    if (!Array.isArray(values)) {
      return this.values();
    }
    for (const value of values) {
      this.add(value);
    }
    return this.values();
  }

  replace(values) {
    const list = Array.isArray(values) ? values : [];
    const next = [];
    const seen = new Set();

    for (const value of list) {
      const prepared = this.#prepareValue(value);
      if (prepared == null) {
        continue;
      }
      const key = this.#normalizeValue(prepared);
      if (!key || seen.has(key)) {
        continue;
      }
      seen.add(key);
      next.push(prepared);
      if (this.#maxEntries && next.length >= this.#maxEntries) {
        break;
      }
    }

    this.#entries = next;
    return this.values();
  }

  toJSON() {
    return this.values();
  }

  #prepareValue(value) {
    try {
      const prepared = this.#prepare(value);
      return prepared ?? null;
    } catch {
      return null;
    }
  }

  #normalizeValue(value) {
    const normalized = (() => {
      try {
        return this.#normalize(value);
      } catch {
        return "";
      }
    })();
    if (typeof normalized !== "string") {
      if (normalized == null) {
        return "";
      }
      return `${normalized}`.trim().toLowerCase();
    }
    return normalized.trim();
  }

  #applyLimit(entries) {
    if (!this.#maxEntries) {
      return entries;
    }
    if (entries.length <= this.#maxEntries) {
      return entries;
    }
    return entries.slice(0, this.#maxEntries);
  }
}

export const createAutocompleteHistory = (options) =>
  new AutocompleteHistory(options);
