import { requestScrollForceBottom } from "./scroll-manager.js";
import { normalizeStreamId } from "./stream-id.js";

function buildSnapshotDetail(session) {
  if (!session) {
    return {
      type: "statuschange",
      status: "idle",
      previousStatus: "idle",
      previousMsgId: null,
      currentMsgId: null,
      entryId: null,
      streaming: false,
      reason: null,
      result: null,
      session: null,
    };
  }

  const snapshot = typeof session.snapshot === "function" ? session.snapshot() : null;
  const status = snapshot?.status ?? session.status ?? "idle";
  const currentMsgId = snapshot?.currentMsgId ?? session.currentMsgId ?? null;
  const streaming = snapshot?.streaming ?? session.isStreaming ?? false;

  return {
    type: "statuschange",
    status,
    previousStatus: status,
    previousMsgId: currentMsgId,
    currentMsgId,
    entryId: currentMsgId,
    streaming,
    reason: snapshot?.reason ?? null,
    result: null,
    session,
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
  #session = null;
  #entries = null;
  #forms = new Set();
  #streams = new Map();
  #statusUnsubscribe = null;

  constructor(session) {
    this.#session = session || null;
    if (this.#session && typeof this.#session.onStatusChange === "function") {
      this.#statusUnsubscribe = this.#session.onStatusChange((detail) =>
        this.#handleStatusChange(detail)
      );
    }
  }

  get session() {
    return this.#session;
  }

  dispose() {
    if (this.#statusUnsubscribe) {
      this.#statusUnsubscribe();
      this.#statusUnsubscribe = null;
    }
    this.#forms.clear();
    this.#streams.clear();
    this.#entries = null;
  }

  setEntries(entries) {
    this.#entries = entries || null;
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
    const detail = buildSnapshotDetail(this.#session);
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
    this.#session?.begin(id, { reason });
    requestScrollForceBottom({ source: "stream:start" });
  }

  notifyStreamAbort(stream, { reason = "user:abort" } = {}) {
    const id = normalizeStreamId(stream?.entryId);
    return this.#session?.abort({ reason, entryId: id }) ?? false;
  }

  notifyStreamComplete(stream, { status, reason, entryId } = {}) {
    const id = normalizeStreamId(entryId ?? stream?.entryId);
    if (id && this.#streams.get(id) === stream) {
      this.#streams.delete(id);
    }
    const completionReason = reason || defaultCompletionReason(status);
    this.#session?.complete({ result: status, reason: completionReason, entryId: id });
    if (status !== "aborted") {
      requestScrollForceBottom({ source: "stream:complete" });
    }
  }

  abortActiveStream({ reason = "user:stop" } = {}) {
    const id = normalizeStreamId(this.#session?.currentMsgId);
    if (!id) {
      return false;
    }
    const stream = this.#streams.get(id);
    if (stream && typeof stream.abort === "function") {
      stream.abort({ reason });
      return true;
    }
    return this.#session?.abort({ reason, entryId: id }) ?? false;
  }

  refresh() {
    const detail = buildSnapshotDetail(this.#session);
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
