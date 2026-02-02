import { requestScrollForceBottom } from "./scroll-manager.js";
import { normalizeStreamId } from "./stream-id.js";

const STATUS_IDLE = "idle";
const STATUS_STREAMING = "streaming";
const STATUS_ABORTING = "aborting";

function buildSnapshotDetail(state, overrides = {}) {
  const status = state?.status ?? STATUS_IDLE;
  const currentMsgId = state?.currentMsgId ?? null;
  const streaming = status === STATUS_STREAMING;
  return {
    type: "statuschange",
    status,
    previousStatus: status,
    previousMsgId: currentMsgId,
    currentMsgId,
    entryId: currentMsgId,
    streaming,
    reason: state?.reason ?? null,
    result: null,
    session: null,
    ...overrides,
  };
}

function defaultCompletionReason(status) {
  switch (status) {
    case "aborted":
      return "stream:aborted";
    case "error":
      return "stream:error";
    default:
      return "stream:complete";
  }
}

export class StreamController {
  #entries = null;
  #forms = new Set();
  #streams = new Map();
  #state = {
    status: STATUS_IDLE,
    currentMsgId: null,
    reason: null,
  };

  constructor() {}

  dispose() {
    this.#forms.clear();
    this.#streams.clear();
    this.#entries = null;
    this.#state = {
      status: STATUS_IDLE,
      currentMsgId: null,
      reason: null,
    };
  }

  setEntries(entries) {
    this.#entries = entries || null;
    const current = normalizeStreamId(entries?.dataset?.currentStream ?? null);
    this.#state = {
      status: current ? STATUS_STREAMING : STATUS_IDLE,
      currentMsgId: current,
      reason: null,
    };
    this.refresh();
    return () => {
      if (this.#entries === entries) {
        this.#entries = null;
      }
    };
  }

  registerForm(form) {
    if (!form) {
      return () => {};
    }
    this.#forms.add(form);
    const detail = buildSnapshotDetail(this.#state);
    if (typeof form.handleStreamStatus === "function") {
      form.handleStreamStatus(detail);
    }
    return () => {
      this.#forms.delete(form);
    };
  }

  registerStream(stream) {
    if (!stream) {
      return () => {};
    }
    this.#updateStreamIndex(stream);
    return () => {
      const id = normalizeStreamId(stream?.entryId);
      if (id && this.#streams.get(id) === stream) {
        this.#streams.delete(id);
      }
    };
  }

  notifyStreamStart(stream, { reason = "stream:start" } = {}) {
    const id = normalizeStreamId(stream?.entryId);
    if (id) {
      this.#streams.set(id, stream);
    }
    const previous = { ...this.#state };
    this.#state = {
      status: STATUS_STREAMING,
      currentMsgId: id,
      reason,
    };
    this.#handleStatusChange(
      buildSnapshotDetail(this.#state, {
        type: "begin",
        previousStatus: previous.status,
        previousMsgId: previous.currentMsgId,
        currentMsgId: id,
        entryId: id,
        reason,
      })
    );
    requestScrollForceBottom({ source: "stream:start" });
  }

  notifyStreamAbort(stream, { reason = "user:abort" } = {}) {
    const id = normalizeStreamId(stream?.entryId);
    if (!id && !this.#state.currentMsgId) {
      return false;
    }
    const targetId = id || this.#state.currentMsgId;
    const previous = { ...this.#state };
    this.#state = {
      status: STATUS_ABORTING,
      currentMsgId: null,
      reason,
    };
    this.#handleStatusChange(
      buildSnapshotDetail(this.#state, {
        type: "abort",
        previousStatus: previous.status,
        previousMsgId: previous.currentMsgId,
        currentMsgId: null,
        entryId: targetId,
        reason,
      })
    );
    return true;
  }

  notifyStreamComplete(stream, { status, reason, entryId } = {}) {
    const id = normalizeStreamId(entryId ?? stream?.entryId);
    if (id && this.#streams.get(id) === stream) {
      this.#streams.delete(id);
    }
    const completionReason = reason || defaultCompletionReason(status);
    const previous = { ...this.#state };
    const nextStatus = status === "done" ? STATUS_IDLE : status || STATUS_IDLE;
    this.#state = {
      status: nextStatus,
      currentMsgId: null,
      reason: completionReason,
    };
    this.#handleStatusChange(
      buildSnapshotDetail(this.#state, {
        type: "complete",
        previousStatus: previous.status,
        previousMsgId: previous.currentMsgId,
        currentMsgId: null,
        entryId: id ?? previous.currentMsgId,
        reason: completionReason,
        result: status || "done",
      })
    );
    if (status !== "aborted") {
      requestScrollForceBottom({ source: "stream:complete" });
    }
  }

  abortActiveStream({ reason = "user:stop" } = {}) {
    const id = normalizeStreamId(this.#state.currentMsgId);
    if (!id) {
      return false;
    }
    const stream = this.#streams.get(id);
    if (stream && typeof stream.abort === "function") {
      stream.abort({ reason });
      return true;
    }
    return this.notifyStreamAbort(stream, { reason });
  }

  refresh() {
    const detail = buildSnapshotDetail(this.#state);
    this.#handleStatusChange(detail);
  }

  #handleStatusChange(detail = {}) {
    const msgId = normalizeStreamId(detail.currentMsgId);
    this.#syncEntriesDataset(msgId);
    this.#forms.forEach((form) => {
      if (typeof form.handleStreamStatus === "function") {
        form.handleStreamStatus(detail);
      }
    });
    return detail;
  }

  #syncEntriesDataset(msgId) {
    if (!this.#entries || !this.#entries.dataset) {
      return;
    }
    if (msgId) {
      this.#entries.dataset.currentStream = msgId;
    } else {
      delete this.#entries.dataset.currentStream;
    }
  }

  #updateStreamIndex(stream) {
    const id = normalizeStreamId(stream?.entryId);
    if (!id) {
      return;
    }
    this.#streams.set(id, stream);
  }
}
