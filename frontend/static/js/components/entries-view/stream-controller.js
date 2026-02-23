import { requestScrollForceEdge } from "../../scroll-manager.js";
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

  notifyStreamStart(stream, { reason: _reason = "stream:start" } = {}) {
    const id = normalizeStreamId(stream?.entryId);
    this.#transition(
      "begin",
      {
        status: STATUS_STREAMING,
        currentMsgId: id,
      },
      {
        currentMsgId: id,
        entryId: id,
      },
    );
    requestScrollForceEdge({ source: "stream:start", direction: "down" });
  }

  notifyStreamAbort(stream, { reason: _reason = "user:abort" } = {}) {
    const id = normalizeStreamId(stream?.entryId);
    if (!id && !this.#state.currentMsgId) {
      return;
    }
    const targetId = id || this.#state.currentMsgId;
    this.#transition(
      "abort",
      {
        status: STATUS_ABORTING,
        currentMsgId: null,
      },
      {
        currentMsgId: null,
        entryId: targetId,
      },
    );
  }

  notifyStreamComplete(stream, { status, reason: _reason, entryId } = {}) {
    const id = normalizeStreamId(entryId ?? stream?.entryId);
    const nextStatus = status === "done" ? STATUS_IDLE : status || STATUS_IDLE;
    const previousMsgId = this.#state.currentMsgId;
    this.#transition(
      "complete",
      {
        status: nextStatus,
        currentMsgId: null,
      },
      {
        currentMsgId: null,
        entryId: id ?? previousMsgId,
        result: status || "done",
      },
    );
    if (status !== "aborted") {
      requestScrollForceEdge({ source: "stream:complete", direction: "down" });
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

  #transition(type, nextState, detailOverrides = {}) {
    const previous = { ...this.#state };
    this.#state = nextState;
    return this.#handleStatusChange(
      buildSnapshotDetail(this.#state, {
        type,
        previousStatus: previous.status,
        previousMsgId: previous.currentMsgId,
        ...detailOverrides,
      }),
    );
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
