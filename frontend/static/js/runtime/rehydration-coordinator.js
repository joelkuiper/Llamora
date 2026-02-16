const FRAME_REQUEST =
  globalThis.requestAnimationFrame?.bind(globalThis) || ((callback) => setTimeout(callback, 16));

if (!globalThis.__appRuntime) {
  globalThis.__appRuntime = {};
}

const globalState = globalThis.__appRuntime;

if (!globalState.rehydrationCoordinator) {
  globalState.rehydrationCoordinator = {
    pending: new Map(),
    rafId: null,
    nextRequestId: 1,
  };
}

const coordinatorState = globalState.rehydrationCoordinator;

function normalizeRegionId(regionId) {
  const value = String(regionId || "").trim();
  return value || "document";
}

function inferRegionId(sourceEvent) {
  const target = sourceEvent?.detail?.target;
  if (target instanceof Element) {
    return target.id || "document";
  }
  return "document";
}

function buildPayload(item) {
  return {
    reason: item.reason,
    regionId: item.regionId,
    requestId: item.requestId,
    isCoalesced: item.count > 1,
  };
}

function flushFrame() {
  coordinatorState.rafId = null;
  const batch = Array.from(coordinatorState.pending.values());
  coordinatorState.pending.clear();

  batch.forEach((item) => {
    document.dispatchEvent(new CustomEvent("app:rehydrate", { detail: buildPayload(item) }));
  });
}

function scheduleFlush() {
  if (coordinatorState.rafId !== null) {
    return;
  }
  coordinatorState.rafId = FRAME_REQUEST(flushFrame);
}

export function requestRehydrate({ reason = "manual", regionId = "document" } = {}) {
  const normalizedRegionId = normalizeRegionId(regionId);
  const existing = coordinatorState.pending.get(normalizedRegionId);

  if (existing) {
    existing.count += 1;
    existing.reason = String(reason || existing.reason);
  } else {
    coordinatorState.pending.set(normalizedRegionId, {
      reason: String(reason || "manual"),
      regionId: normalizedRegionId,
      requestId: coordinatorState.nextRequestId,
      count: 1,
    });
    coordinatorState.nextRequestId += 1;
  }

  scheduleFlush();
}

export function ingestSourceEvent(sourceEvent, options = {}) {
  requestRehydrate({
    reason: options.reason || "htmx-after-settle",
    regionId: options.regionId || inferRegionId(sourceEvent),
  });
}
