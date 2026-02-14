(() => {
  const API_BASE = "/api/lockbox";
  const MAX_CACHE_SIZE = 256;
  const cache = new Map();

  function assertToken(value, label) {
    if (typeof value !== "string") {
      throw new Error(`Invalid ${label}: expected a string.`);
    }
    if (value.length < 1 || value.length > 128) {
      throw new Error(`Invalid ${label}: must be 1-128 characters.`);
    }
    for (let index = 0; index < value.length; index += 1) {
      if (value.charCodeAt(index) > 127) {
        throw new Error(`Invalid ${label}: must contain ASCII characters only.`);
      }
    }
  }

  function endpoint(namespace, key) {
    const ns = encodeURIComponent(namespace);
    if (key === undefined) {
      return `${API_BASE}/${ns}`;
    }
    const k = encodeURIComponent(key);
    return `${API_BASE}/${ns}/${k}`;
  }

  function cacheKey(namespace, key) {
    return `${namespace}\u0000${key}`;
  }

  function getCached(namespace, key) {
    const ck = cacheKey(namespace, key);
    if (!cache.has(ck)) {
      return undefined;
    }
    const value = cache.get(ck);
    cache.delete(ck);
    cache.set(ck, value);
    return value;
  }

  function setCached(namespace, key, value) {
    const ck = cacheKey(namespace, key);
    if (cache.has(ck)) {
      cache.delete(ck);
    }
    cache.set(ck, value);
    if (cache.size > MAX_CACHE_SIZE) {
      const oldest = cache.keys().next().value;
      cache.delete(oldest);
    }
  }

  function clearCached(namespace, key) {
    cache.delete(cacheKey(namespace, key));
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
    });

    if (!response.ok) {
      throw new Error(`Lockbox request failed: ${response.status} ${path}`);
    }

    return response.json();
  }

  async function set(namespace, key, value) {
    assertToken(namespace, "namespace");
    assertToken(key, "key");

    const path = endpoint(namespace, key);
    const body = JSON.stringify({ value });
    await request(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body,
    });

    clearCached(namespace, key);
    return true;
  }

  async function get(namespace, key) {
    assertToken(namespace, "namespace");
    assertToken(key, "key");

    const cached = getCached(namespace, key);
    if (cached !== undefined) {
      return cached;
    }

    const path = endpoint(namespace, key);
    const payload = await request(path, { method: "GET" });
    const value = payload.ok ? payload.value : null;
    setCached(namespace, key, value);
    return value;
  }

  async function remove(namespace, key) {
    assertToken(namespace, "namespace");
    assertToken(key, "key");

    const path = endpoint(namespace, key);
    await request(path, { method: "DELETE" });

    clearCached(namespace, key);
    return true;
  }

  async function list(namespace) {
    assertToken(namespace, "namespace");

    const path = endpoint(namespace);
    const payload = await request(path, { method: "GET" });
    return Array.isArray(payload.keys) ? payload.keys : [];
  }

  window.lockbox = {
    set,
    get,
    delete: remove,
    list,
  };
})();
