let cachedState = null;
let cachedElement = null;
let cachedRaw = "";

const parseViewStatePayload = (raw) => {
  if (!raw) return null;
  try {
    const data = JSON.parse(raw);
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
};

const readViewStateElement = (root = document) => {
  const fromRoot = root?.querySelector?.("#view-state");
  if (fromRoot instanceof HTMLScriptElement) return fromRoot;
  const global = document.getElementById("view-state");
  return global instanceof HTMLScriptElement ? global : null;
};

const normalizeViewState = (state) => {
  if (!state || typeof state !== "object") return null;
  const view = String(state.view || "").trim() || "diary";
  const day = String(state.day || "").trim() || null;
  const selectedTag = String(state.selected_tag || "").trim() || null;
  const sortKind = String(state.sort_kind || "").trim() || null;
  const sortDir = String(state.sort_dir || "").trim() || null;
  const target = String(state.target || "").trim() || null;
  return {
    ...state,
    view,
    day,
    selected_tag: selectedTag,
    sort_kind: sortKind,
    sort_dir: sortDir,
    target,
  };
};

export const hydrateViewState = (root = document) => {
  const el = readViewStateElement(root);
  if (!el) return cachedState;
  const raw = el.textContent || "";
  if (cachedElement === el && cachedState && raw === cachedRaw) return cachedState;
  const payload = parseViewStatePayload(raw);
  const normalized = normalizeViewState(payload);
  if (!normalized) return cachedState;
  cachedElement = el;
  cachedState = normalized;
  cachedRaw = raw;
  document.dispatchEvent(
    new CustomEvent("app:view-state-changed", {
      detail: { state: cachedState },
    }),
  );
  document.dispatchEvent(
    new CustomEvent("app:view-changed", {
      detail: { view: cachedState.view },
    }),
  );
  return cachedState;
};

export const getViewState = () => {
  if (cachedState) return cachedState;
  return hydrateViewState(document) || { view: "diary" };
};

export const getViewStateValue = (key, fallback = "") => {
  const state = getViewState();
  if (!state || typeof state !== "object") return fallback;
  return state[key] ?? fallback;
};

export default getViewState;
