const DEFAULT_EDGE_BUFFER = 24;
const DEFAULT_EDGE_THRESHOLD = {
  down: 150,
  up: 110,
};
const DEFAULT_EDGE_OFFSET = {
  down: 120,
  up: 88,
};

const normalizeEdgeDirection = (value, fallback = "down") => {
  const normalized = String(value || "")
    .trim()
    .toLowerCase();
  if (normalized === "up" || normalized === "top") {
    return "up";
  }
  if (normalized === "down" || normalized === "bottom") {
    return "down";
  }
  return fallback;
};

const readNumericDataset = (button, key) => {
  if (!button?.dataset) return null;
  const raw = button.dataset[key];
  if (raw == null || raw === "") return null;
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : null;
};

const resolveEdgeAvoidElement = (button, root = document) => {
  const selector = button?.dataset?.edgeAvoid;
  if (!selector) return null;
  try {
    return root.querySelector(selector);
  } catch {
    return null;
  }
};

const resolveEdgeCenterElement = (button, root = document, fallback = null) => {
  const selector = button?.dataset?.edgeCenter;
  if (selector) {
    try {
      const found = root.querySelector(selector);
      if (found) return found;
    } catch {
      // ignore
    }
  }
  return fallback instanceof Element ? fallback : null;
};

const computeEdgeMetrics = ({
  button,
  root = document,
  fallbackCenter = null,
  fallbackDirection = "down",
} = {}) => {
  const direction = normalizeEdgeDirection(
    button?.dataset?.direction || button?.dataset?.edgeDirection,
    fallbackDirection,
  );
  const buffer = readNumericDataset(button, "edgeBuffer") ?? DEFAULT_EDGE_BUFFER;
  const thresholdOverride = readNumericDataset(button, "edgeThreshold");
  const offsetOverride = readNumericDataset(button, "edgeOffset");

  const avoidEl = resolveEdgeAvoidElement(button, root);
  const avoidHeight = avoidEl instanceof Element ? avoidEl.getBoundingClientRect().height : 0;
  const avoidMetric = avoidHeight > 0 ? avoidHeight + buffer : 0;

  const fallbackThreshold = DEFAULT_EDGE_THRESHOLD[direction] ?? DEFAULT_EDGE_THRESHOLD.down;
  const fallbackOffset = DEFAULT_EDGE_OFFSET[direction] ?? DEFAULT_EDGE_OFFSET.down;

  const threshold = thresholdOverride ?? (avoidMetric || fallbackThreshold);
  const offset = offsetOverride ?? (avoidMetric || fallbackOffset);

  const centerEl = resolveEdgeCenterElement(button, root, fallbackCenter);
  const centerPx = centerEl
    ? centerEl.getBoundingClientRect().left + centerEl.getBoundingClientRect().width / 2
    : null;

  return {
    direction,
    threshold,
    offset,
    centerPx,
  };
};

const applyEdgeMetrics = (button, metrics) => {
  if (!button || !metrics) return;
  if (Number.isFinite(metrics.centerPx)) {
    button.style.setProperty("--scroll-edge-center", `${metrics.centerPx}px`);
  }
  if (Number.isFinite(metrics.offset)) {
    button.style.setProperty("--scroll-edge-offset", `${metrics.offset}px`);
  }
};

export {
  DEFAULT_EDGE_BUFFER,
  DEFAULT_EDGE_THRESHOLD,
  DEFAULT_EDGE_OFFSET,
  normalizeEdgeDirection,
  computeEdgeMetrics,
  applyEdgeMetrics,
};
