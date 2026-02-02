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
    result: null,
    ...overrides,
  };
}

export class StreamController {
  #entries = null;
  #forms = new Set();
  #state = {
    status: STATUS_IDLE,
    currentMsgId: null,
  };

  constructor() {}

  dispose() {
    this.#forms.clear();
    this.#entries = null;
    this.#state = {
      status: STATUS_IDLE,
      currentMsgId: null,
    };
  }

  setEntries(entries) {
    this.#entries = entries || null;
    const current = normalizeStreamId(entries?.dataset?.currentStream ?? null);
    this.#state = {
      status: current ? STATUS_STREAMING : STATUS_IDLE,
      currentMsgId: current,
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

  notifyStreamStart(stream, { reason = "stream:start" } = {}) {
    const id = normalizeStreamId(stream?.entryId);
    const previous = { ...this.#state };
    this.#state = {
      status: STATUS_STREAMING,
      currentMsgId: id,
    };
    this.#handleStatusChange(
      buildSnapshotDetail(this.#state, {
        type: "begin",
        previousStatus: previous.status,
        previousMsgId: previous.currentMsgId,
        currentMsgId: id,
        entryId: id,
      })
    );
    requestScrollForceBottom({ source: "stream:start" });
  }

  notifyStreamAbort(stream, { reason = "user:abort" } = {}) {
    const id = normalizeStreamId(stream?.entryId);
    if (!id && !this.#state.currentMsgId) {
      return;
    }
    const targetId = id || this.#state.currentMsgId;
    const previous = { ...this.#state };
    this.#state = {
      status: STATUS_ABORTING,
      currentMsgId: null,
    };
    this.#handleStatusChange(
      buildSnapshotDetail(this.#state, {
        type: "abort",
        previousStatus: previous.status,
        previousMsgId: previous.currentMsgId,
        currentMsgId: null,
        entryId: targetId,
      })
    );
  }

  notifyStreamComplete(stream, { status, reason, entryId } = {}) {
    const id = normalizeStreamId(entryId ?? stream?.entryId);
    const previous = { ...this.#state };
    const nextStatus = status === "done" ? STATUS_IDLE : status || STATUS_IDLE;
    this.#state = {
      status: nextStatus,
      currentMsgId: null,
    };
    this.#handleStatusChange(
      buildSnapshotDetail(this.#state, {
        type: "complete",
        previousStatus: previous.status,
        previousMsgId: previous.currentMsgId,
        currentMsgId: null,
        entryId: id ?? previous.currentMsgId,
        result: status || "done",
      })
    );
    if (status !== "aborted") {
      requestScrollForceBottom({ source: "stream:complete" });
    }
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

}
