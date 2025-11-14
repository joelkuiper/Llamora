export class StreamingSession extends EventTarget {
  #currentMsgId = null;
  #status = "idle";

  constructor({ currentMsgId = null } = {}) {
    super();
    if (currentMsgId) {
      this.#currentMsgId = String(currentMsgId);
      this.#status = "streaming";
    }
  }

  get currentMsgId() {
    return this.#currentMsgId;
  }

  get status() {
    return this.#status;
  }

  get isStreaming() {
    return Boolean(this.#currentMsgId);
  }

  begin(userMsgId) {
    const normalized = userMsgId ? String(userMsgId) : null;
    if (!normalized) {
      return;
    }

    if (normalized === this.#currentMsgId && this.#status === "streaming") {
      return;
    }

    this.#currentMsgId = normalized;
    this.#status = "streaming";

    this.dispatchEvent(
      new CustomEvent("streaming:begin", {
        detail: { userMsgId: normalized },
      })
    );
  }

  abort() {
    if (!this.#currentMsgId) {
      return false;
    }

    if (this.#status === "aborting") {
      return true;
    }

    this.#status = "aborting";
    const userMsgId = this.#currentMsgId;

    this.dispatchEvent(
      new CustomEvent("streaming:abort", {
        detail: { userMsgId },
      })
    );

    return true;
  }

  complete(status = "done") {
    const finalStatus = status || "done";
    const userMsgId = this.#currentMsgId;

    this.#currentMsgId = null;
    this.#status = finalStatus === "done" ? "idle" : finalStatus;

    this.dispatchEvent(
      new CustomEvent("streaming:complete", {
        detail: { status: finalStatus, userMsgId },
      })
    );
  }
}
