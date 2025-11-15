import { requestScrollForceBottom } from "./scroll-manager.js";

function normalizeId(value) {
  return value ? String(value) : null;
}

function buildSnapshotDetail(session) {
  if (!session) {
    return {
      type: "statuschange",
      status: "idle",
      previousStatus: "idle",
      previousMsgId: null,
      currentMsgId: null,
      userMsgId: null,
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
    userMsgId: currentMsgId,
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
  #chat = null;
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
    this.#chat = null;
  }

  setChat(chat) {
    this.#chat = chat || null;
    this.refresh();
    return () => {
      if (this.#chat === chat) {
        this.#chat = null;
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
      const id = normalizeId(stream?.userMsgId);
      if (id && this.#streams.get(id) === stream) {
        this.#streams.delete(id);
      }
    };
  }

  notifyStreamStart(stream, { reason = "stream:start" } = {}) {
    const id = normalizeId(stream?.userMsgId);
    if (id) {
      this.#streams.set(id, stream);
    }
    this.#session?.begin(id, { reason });
    requestScrollForceBottom({ source: "stream:start" });
  }

  notifyStreamAbort(stream, { reason = "user:abort" } = {}) {
    const id = normalizeId(stream?.userMsgId);
    return this.#session?.abort({ reason, userMsgId: id }) ?? false;
  }

  notifyStreamComplete(stream, { status, reason, userMsgId } = {}) {
    const id = normalizeId(userMsgId ?? stream?.userMsgId);
    if (id && this.#streams.get(id) === stream) {
      this.#streams.delete(id);
    }
    const completionReason = reason || defaultCompletionReason(status);
    this.#session?.complete({ result: status, reason: completionReason, userMsgId: id });
    if (status !== "aborted") {
      requestScrollForceBottom({ source: "stream:complete" });
    }
  }

  abortActiveStream({ reason = "user:stop" } = {}) {
    const id = normalizeId(this.#session?.currentMsgId);
    if (!id) {
      return false;
    }
    const stream = this.#streams.get(id);
    if (stream && typeof stream.abort === "function") {
      stream.abort({ reason });
      return true;
    }
    return this.#session?.abort({ reason, userMsgId: id }) ?? false;
  }

  refresh() {
    const detail = buildSnapshotDetail(this.#session);
    this.#handleStatusChange(detail);
  }

  #handleStatusChange(detail = {}) {
    const msgId = normalizeId(detail.currentMsgId);
    this.#syncChatDataset(msgId);
    this.#forms.forEach((form) => {
      if (typeof form.handleStreamStatus === "function") {
        form.handleStreamStatus(detail);
      }
    });
    return detail;
  }

  #syncChatDataset(msgId) {
    if (!this.#chat || !this.#chat.dataset) {
      return;
    }
    if (msgId) {
      this.#chat.dataset.currentStream = msgId;
    } else {
      delete this.#chat.dataset.currentStream;
    }
  }

  #updateStreamIndex(stream) {
    const id = normalizeId(stream?.userMsgId);
    if (!id) {
      return;
    }
    this.#streams.set(id, stream);
  }
}
