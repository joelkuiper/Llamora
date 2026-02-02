const STATUS_IDLE = "idle";
const STATUS_STREAMING = "streaming";
const STATUS_ABORTING = "aborting";

function normalizeMsgId(value) {
  return value ? String(value) : null;
}

function createDetail(session, overrides = {}) {
  return {
    type: "statuschange",
    status: STATUS_IDLE,
    previousStatus: STATUS_IDLE,
    previousMsgId: null,
    currentMsgId: null,
    entryId: null,
    streaming: false,
    reason: null,
    result: null,
    session,
    ...overrides,
  };
}

export class StreamingSession extends EventTarget {
  #state = {
    status: STATUS_IDLE,
    currentMsgId: null,
    reason: null,
  };

  constructor({ currentMsgId = null, status = STATUS_IDLE } = {}) {
    super();
    const normalized = normalizeMsgId(currentMsgId);
    if (normalized) {
      this.#state = {
        status: status === STATUS_ABORTING ? STATUS_ABORTING : STATUS_STREAMING,
        currentMsgId: normalized,
        reason: null,
      };
    }
  }

  get currentMsgId() {
    return this.#state.currentMsgId;
  }

  get status() {
    return this.#state.status;
  }

  get isStreaming() {
    return this.#state.status === STATUS_STREAMING;
  }

  snapshot() {
    return {
      status: this.status,
      currentMsgId: this.currentMsgId,
      streaming: this.isStreaming,
      reason: this.#state.reason,
    };
  }

  setStatus(status, detail = {}) {
    return this.#transition(status || STATUS_IDLE, detail);
  }

  onStatusChange(callback, { signal } = {}) {
    if (typeof callback !== "function") {
      return () => {};
    }

    const handler = (event) => {
      callback(event?.detail || {}, event);
    };

    this.addEventListener("streaming:statuschange", handler, { signal });

    return () => {
      this.removeEventListener("streaming:statuschange", handler);
    };
  }

  begin(entryId, { reason = "stream:start" } = {}) {
    const normalized = normalizeMsgId(entryId);
    if (!normalized) {
      return false;
    }

    if (
      this.#state.currentMsgId === normalized &&
      this.#state.status === STATUS_STREAMING
    ) {
      return false;
    }

    const detail = this.#transition(STATUS_STREAMING, {
      type: "begin",
      entryId: normalized,
      currentMsgId: normalized,
      reason,
    });

    this.dispatchEvent(
      new CustomEvent("streaming:begin", {
        detail,
      })
    );

    return true;
  }

  abort({ reason = "user:abort", entryId = null } = {}) {
    if (!this.#state.currentMsgId && !entryId) {
      return false;
    }

    if (this.#state.status === STATUS_ABORTING) {
      return true;
    }

    const targetId =
      normalizeMsgId(entryId) || this.#state.currentMsgId || null;
    if (!targetId) {
      return false;
    }

    const detail = this.#transition(STATUS_ABORTING, {
      type: "abort",
      entryId: targetId,
      currentMsgId: null,
      reason,
    });

    this.dispatchEvent(
      new CustomEvent("streaming:abort", {
        detail,
      })
    );

    return true;
  }

  complete({ result = "done", reason = "stream:complete", entryId } = {}) {
    const finalResult = result || "done";
    const targetId =
      normalizeMsgId(entryId) || this.#state.currentMsgId || null;

    const nextStatus = finalResult === "done" ? STATUS_IDLE : finalResult;

    const detail = this.#transition(nextStatus, {
      type: "complete",
      entryId: targetId,
      currentMsgId: null,
      reason,
      result: finalResult,
    });

    this.dispatchEvent(
      new CustomEvent("streaming:complete", {
        detail,
      })
    );
  }

  #transition(status, detail = {}) {
    const nextStatus = status || STATUS_IDLE;
    const previous = { ...this.#state };

    let nextCurrent = previous.currentMsgId;
    if (Object.prototype.hasOwnProperty.call(detail, "currentMsgId")) {
      nextCurrent = detail.currentMsgId;
    } else if (nextStatus === STATUS_IDLE) {
      nextCurrent = null;
    }

    const nextReason = detail.reason ?? null;

    this.#state = {
      status: nextStatus,
      currentMsgId: nextCurrent,
      reason: nextReason,
    };

    const payload = createDetail(this, {
      type: detail.type || "statuschange",
      status: nextStatus,
      previousStatus: previous.status,
      previousMsgId: previous.currentMsgId,
      currentMsgId: this.#state.currentMsgId,
      entryId:
        detail.entryId ?? this.#state.currentMsgId ?? previous.currentMsgId ?? null,
      streaming: nextStatus === STATUS_STREAMING,
      reason: nextReason,
      result: detail.result ?? null,
      session: this,
    });

    this.dispatchEvent(
      new CustomEvent("streaming:statuschange", {
        detail: payload,
      })
    );

    return payload;
  }
}
