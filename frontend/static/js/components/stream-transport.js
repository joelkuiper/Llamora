const NEWLINE_REGEX = /\[newline\]/g;

function decodeChunk(data) {
  return typeof data === "string" ? data.replace(NEWLINE_REGEX, "\n") : "";
}

function parseDonePayload(data) {
  if (!data) return {};
  try {
    return JSON.parse(data);
  } catch (err) {
    console.error("Failed to parse completion payload", err);
    return {};
  }
}

function parseMetaPayload(data) {
  if (!data) return null;
  try {
    return JSON.parse(data);
  } catch (err) {
    console.error("Failed to parse meta payload", err);
    return null;
  }
}

export class StreamTransport {
  #eventSource = null;
  #listeners = null;
  #url = "";
  #onChunk;
  #onDone;
  #onError;
  #onMeta;

  constructor({ url, onChunk, onDone, onError, onMeta } = {}) {
    this.#url = url || "";
    this.#onChunk = onChunk || null;
    this.#onDone = onDone || null;
    this.#onError = onError || null;
    this.#onMeta = onMeta || null;
  }

  get active() {
    return Boolean(this.#eventSource);
  }

  start() {
    if (this.#eventSource || !this.#url) return;
    this.#eventSource = new EventSource(this.#url, { withCredentials: true });
    this.#listeners = {
      message: (event) => {
        const chunk = decodeChunk(event?.data || "");
        if (!chunk) return;
        this.#onChunk?.(chunk);
      },
      done: (event) => {
        const payload = parseDonePayload(event?.data || "");
        this.#onDone?.(payload);
      },
      error: (event) => {
        const data = decodeChunk(event?.data || "");
        this.#onError?.(data);
      },
      meta: (event) => {
        const raw = decodeChunk(event?.data || "");
        if (!raw) return;
        const meta = parseMetaPayload(raw);
        if (!meta) return;
        this.#onMeta?.(meta);
      },
    };

    this.#eventSource.addEventListener("message", this.#listeners.message);
    this.#eventSource.addEventListener("done", this.#listeners.done);
    this.#eventSource.addEventListener("error", this.#listeners.error);
    this.#eventSource.addEventListener("meta", this.#listeners.meta);
  }

  close() {
    if (!this.#eventSource) return;
    if (this.#listeners) {
      this.#eventSource.removeEventListener("message", this.#listeners.message);
      this.#eventSource.removeEventListener("done", this.#listeners.done);
      this.#eventSource.removeEventListener("error", this.#listeners.error);
      this.#eventSource.removeEventListener("meta", this.#listeners.meta);
    }
    this.#eventSource.close();
    this.#eventSource = null;
    this.#listeners = null;
  }
}
