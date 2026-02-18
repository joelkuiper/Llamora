const TAGS_VIEW_PHASE = Object.freeze({
  IDLE: "IDLE",
  NAVIGATING: "NAVIGATING",
  LOADING_LIST: "LOADING_LIST",
  LOADING_DETAIL: "LOADING_DETAIL",
  SETTLING_LIST: "SETTLING_LIST",
  SETTLING_DETAIL: "SETTLING_DETAIL",
});

const ALLOWED_PHASE_TRANSITIONS = Object.freeze({
  [TAGS_VIEW_PHASE.IDLE]: new Set([
    TAGS_VIEW_PHASE.IDLE,
    TAGS_VIEW_PHASE.NAVIGATING,
    TAGS_VIEW_PHASE.LOADING_LIST,
    TAGS_VIEW_PHASE.LOADING_DETAIL,
  ]),
  [TAGS_VIEW_PHASE.NAVIGATING]: new Set([
    TAGS_VIEW_PHASE.NAVIGATING,
    TAGS_VIEW_PHASE.LOADING_LIST,
    TAGS_VIEW_PHASE.LOADING_DETAIL,
    TAGS_VIEW_PHASE.IDLE,
  ]),
  [TAGS_VIEW_PHASE.LOADING_LIST]: new Set([
    TAGS_VIEW_PHASE.LOADING_LIST,
    TAGS_VIEW_PHASE.SETTLING_LIST,
    TAGS_VIEW_PHASE.IDLE,
  ]),
  [TAGS_VIEW_PHASE.LOADING_DETAIL]: new Set([
    TAGS_VIEW_PHASE.LOADING_DETAIL,
    TAGS_VIEW_PHASE.SETTLING_DETAIL,
    TAGS_VIEW_PHASE.IDLE,
  ]),
  [TAGS_VIEW_PHASE.SETTLING_LIST]: new Set([
    TAGS_VIEW_PHASE.SETTLING_LIST,
    TAGS_VIEW_PHASE.IDLE,
    TAGS_VIEW_PHASE.LOADING_DETAIL,
    TAGS_VIEW_PHASE.LOADING_LIST,
  ]),
  [TAGS_VIEW_PHASE.SETTLING_DETAIL]: new Set([
    TAGS_VIEW_PHASE.SETTLING_DETAIL,
    TAGS_VIEW_PHASE.IDLE,
    TAGS_VIEW_PHASE.LOADING_DETAIL,
    TAGS_VIEW_PHASE.LOADING_LIST,
  ]),
});

const createLifecycleContext = () => ({
  suppressSave: false,
  detailScrollPending: false,
  pendingTagHighlight: "",
  restoreAppliedLocation: "",
  listScrollPending: false,
});

export const state = {
  query: "",
  sortKind: "count",
  sortDir: "desc",
  rows: [],
  input: null,
  clearBtn: null,
  empty: null,
  list: null,
  indexItems: null,
  indexPending: false,
  listBuilt: false,
  indexSignature: "",
  saveFrame: 0,
  listPositions: null,
  machine: {
    phase: TAGS_VIEW_PHASE.IDLE,
    lastEvent: "init",
    context: createLifecycleContext(),
  },
};

export const TagsViewPhase = TAGS_VIEW_PHASE;

export const getPhase = () => state.machine.phase;

export const transitionPhase = (nextPhase, eventName = "") => {
  const current = state.machine.phase;
  const allowed = ALLOWED_PHASE_TRANSITIONS[current];
  if (!allowed?.has(nextPhase)) {
    return false;
  }
  state.machine.phase = nextPhase;
  state.machine.lastEvent = String(eventName || "").trim() || state.machine.lastEvent;
  return true;
};

export const forcePhase = (nextPhase, eventName = "") => {
  state.machine.phase = nextPhase;
  state.machine.lastEvent = String(eventName || "").trim() || state.machine.lastEvent;
};

export const isSaveSuppressed = () => Boolean(state.machine.context.suppressSave);
export const setSaveSuppressed = (value) => {
  state.machine.context.suppressSave = Boolean(value);
};

export const hasPendingDetailScroll = () => Boolean(state.machine.context.detailScrollPending);
export const requestDetailScroll = () => {
  state.machine.context.detailScrollPending = true;
};
export const clearPendingDetailScroll = () => {
  state.machine.context.detailScrollPending = false;
};
export const consumePendingDetailScroll = () => {
  if (!hasPendingDetailScroll()) return false;
  clearPendingDetailScroll();
  return true;
};

export const getPendingTagHighlight = () => state.machine.context.pendingTagHighlight || "";
export const setPendingTagHighlight = (tagName) => {
  state.machine.context.pendingTagHighlight = String(tagName || "").trim();
};
export const clearPendingTagHighlight = () => {
  state.machine.context.pendingTagHighlight = "";
};

export const hasPendingListScroll = () => Boolean(state.machine.context.listScrollPending);
export const requestListScroll = () => {
  state.machine.context.listScrollPending = true;
};
export const clearPendingListScroll = () => {
  state.machine.context.listScrollPending = false;
};
export const consumePendingListScroll = () => {
  if (!hasPendingListScroll()) return false;
  clearPendingListScroll();
  return true;
};

export const resetRestoreAppliedLocation = () => {
  state.machine.context.restoreAppliedLocation = "";
};
export const markRestoreAppliedLocation = (locationKey) => {
  state.machine.context.restoreAppliedLocation = String(locationKey || "").trim();
};
export const hasRestoreAppliedLocation = (locationKey) =>
  state.machine.context.restoreAppliedLocation === String(locationKey || "").trim();
