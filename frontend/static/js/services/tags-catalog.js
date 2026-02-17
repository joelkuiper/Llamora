const MISSING_SIGNATURE = "__missing__";

const state = {
  raw: "",
  version: 0,
  items: [],
};

const findCatalogScript = (root = document) =>
  root.querySelector?.("#tags-catalog-data") || document.getElementById("tags-catalog-data");

const normalizeItem = (item) => {
  const name = String(item?.name || "").trim();
  if (!name) return null;
  return {
    name,
    hash: String(item?.hash || "").trim(),
    count: Math.max(0, Number.parseInt(String(item?.count ?? "0"), 10) || 0),
  };
};

const sanitizeItems = (payload) => {
  if (!Array.isArray(payload)) return [];
  return payload.map(normalizeItem).filter(Boolean);
};

const toSnapshot = () => ({
  version: state.version,
  items: state.items.map((item) => ({ ...item })),
});

const parseRawItems = (raw) => {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return sanitizeItems(Array.isArray(parsed) ? parsed : parsed?.items);
  } catch {
    return [];
  }
};

const commit = (items, script = null) => {
  state.items = sanitizeItems(items);
  state.version += 1;
  if (script instanceof HTMLScriptElement) {
    const raw = JSON.stringify(state.items);
    script.textContent = raw;
    state.raw = raw;
    return;
  }
  state.raw = state.raw || JSON.stringify(state.items);
};

export const readTagsCatalog = (root = document) => {
  const script = findCatalogScript(root);
  if (!(script instanceof HTMLScriptElement)) {
    if (state.raw !== MISSING_SIGNATURE) {
      state.raw = MISSING_SIGNATURE;
      state.items = [];
      state.version += 1;
    }
    return toSnapshot();
  }
  const raw = (script.textContent || "").trim();
  if (raw !== state.raw) {
    state.raw = raw;
    state.items = parseRawItems(raw);
    state.version += 1;
  }
  return toSnapshot();
};

export const getTagsCatalogItems = (root = document) => readTagsCatalog(root).items;

export const getTagsCatalogNames = (root = document) =>
  readTagsCatalog(root).items.map((item) => item.name);

export const applyTagsCatalogCountUpdate = (payload, root = document) => {
  if (!payload || typeof payload !== "object") {
    return toSnapshot();
  }
  const name = String(payload.tag || "").trim();
  if (!name) {
    return toSnapshot();
  }
  const count = Math.max(0, Number.parseInt(String(payload.count ?? "0"), 10) || 0);
  const hash = String(payload.tag_hash || "").trim();

  const script = findCatalogScript(root);
  const snapshot = readTagsCatalog(root);
  const nextItems = snapshot.items.slice();
  const index = nextItems.findIndex((item) => item.name === name);
  if (count <= 0) {
    if (index >= 0) {
      nextItems.splice(index, 1);
    }
  } else if (index >= 0) {
    nextItems[index] = {
      ...nextItems[index],
      count,
      hash: hash || nextItems[index].hash,
    };
  } else {
    nextItems.push({ name, hash, count });
  }
  commit(nextItems, script);
  return toSnapshot();
};
