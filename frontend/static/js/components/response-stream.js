import { requestScrollForceBottom } from "../entries/scroll-manager.js";
import { animateMotion, isMotionReduced } from "../services/motion.js";
import { applyTimezoneQuery, formatLocalTime, formatLocalTimestamp } from "../services/time.js";
import { TYPING_INDICATOR_SELECTOR } from "../typing-indicator.js";
import { StreamRenderer } from "./stream-renderer.js";
import { StreamTransport } from "./stream-transport.js";

const FALLBACK_ERROR_MESSAGE = "The assistant ran into an error. Please try again.";
const REPEAT_GUARD_BADGE = "response trimmed";
const REPEAT_GUARD_DESCRIPTION = "Response paused after repeating itself.";
const REPEAT_GUARD_HIDE_DELAY_MS = 5000;
const NEWLINE_REGEX = /\[newline\]/g;

function decodeChunk(data) {
  return typeof data === "string" ? data.replace(NEWLINE_REGEX, "\n") : "";
}

function requestScrollToBottom(detail = {}) {
  requestScrollForceBottom({ source: "response-stream", ...detail });
}

class ResponseStreamElement extends HTMLElement {
  #renderer = null;
  #completed = false;
  #text = "";
  #sink = null;
  #markdown = null;
  #typingIndicator = null;
  #meta = null;
  #wasPartial = false;
  #repeatGuardIndicator = null;
  #repeatGuardHideTimer = null;
  #repeatGuardWavesDismissed = false;
  #transport = null;
  #controller = null;
  #deleteButton = null;

  constructor() {
    super();
  }

  #getStreamController() {
    if (this.dataset?.controller === "off") {
      return null;
    }
    const host = this.closest?.("entry-view");
    if (host && "streamController" in host) {
      return host.streamController || null;
    }
    return null;
  }

  #syncController() {
    const controller = this.#getStreamController();
    if (controller === this.#controller) {
      return;
    }
    this.#controller = controller || null;
  }

  connectedCallback() {
    this.#sink = this.querySelector(".raw-response");
    this.#markdown = this.querySelector(".markdown-body");
    this.#typingIndicator = this.querySelector(TYPING_INDICATOR_SELECTOR) || null;
    this.#repeatGuardIndicator = this.querySelector(".repeat-guard-indicator") || null;
    this.#deleteButton = this.querySelector(".entry-delete") || null;
    this.#repeatGuardWavesDismissed = Boolean(
      this.#repeatGuardIndicator?.classList.contains("repeat-guard-indicator--calm"),
    );

    if (this.#sink) {
      this.#text = decodeChunk(this.#sink.textContent || "");
    }

    if (this.#markdown && !this.#renderer) {
      this.#renderer = new StreamRenderer({
        markdown: this.#markdown,
        typingIndicator: this.#typingIndicator,
        requestScroll: (detail) => requestScrollToBottom({ ...detail, element: this }),
      });
    } else if (this.#renderer) {
      this.#renderer.setTypingIndicator(this.#typingIndicator);
    }

    this.#syncController();
    this.#syncDeleteButton();

    if (!this.#completed) {
      if (this.#suppressOpeningStream()) {
        return;
      }
      this.#startStream();
    }
  }

  disconnectedCallback() {
    this.#renderer?.cancelRender();
    this.#closeTransport();
    if (this.#repeatGuardHideTimer) {
      clearTimeout(this.#repeatGuardHideTimer);
      this.#repeatGuardHideTimer = null;
    }
    this.#repeatGuardWavesDismissed = false;
    this.#deleteButton = null;
    this.#controller = null;
  }

  get entryId() {
    return this.dataset.entryId || null;
  }

  get sseUrl() {
    return this.dataset.sseUrl || this.getAttribute("sse-url") || "";
  }

  #suppressOpeningStream() {
    const entryId = this.entryId || "";
    const isOpeningStream =
      this.classList.contains("opening-stream") || entryId.startsWith("opening-");
    if (!isOpeningStream) return false;
    const entries = this.closest?.("#entries") || document;
    const hasPersistedOpening = Boolean(
      entries.querySelector(".entry--opening:not(.opening-stream)"),
    );
    if (!hasPersistedOpening) return false;
    this.dataset.streaming = "false";
    this.removeAttribute("data-sse-url");
    this.removeAttribute("aria-busy");
    this.remove();
    return true;
  }

  abort({ reason = "user:abort" } = {}) {
    if (this.#completed) return;
    const controller = this.#controller || this.#getStreamController();
    if (controller && typeof controller.notifyStreamAbort === "function") {
      controller.notifyStreamAbort(this, { reason });
    }

    this.#finalize({ status: "aborted", reason });
  }

  get isStreaming() {
    return this.dataset.streaming === "true";
  }

  isStreamDormant() {
    return this.isStreaming && !this.#transport?.active && !this.#completed;
  }

  resume() {
    if (!this.isStreamDormant()) {
      return false;
    }

    this.#startStream();
    return true;
  }

  handleMarkdownRendered(el) {
    if (!el || el !== this.#markdown) return;
    this.#renderer?.handleMarkdownRendered(el);
  }

  #startStream() {
    if (this.#transport?.active || !this.sseUrl) return;

    const url = applyTimezoneQuery(this.sseUrl);

    this.dataset.sseUrl = url;

    this.dataset.streaming = "true";
    this.setAttribute("aria-busy", "true");
    this.#meta = null;
    this.#wasPartial = false;
    const controller = this.#controller || this.#getStreamController();
    if (controller && typeof controller.notifyStreamStart === "function") {
      controller.notifyStreamStart(this, { reason: "stream:start" });
    }
    requestScrollForceBottom({ source: "stream:start" });
    this.dispatchEvent(
      new CustomEvent("response-stream:start", {
        bubbles: true,
        composed: true,
        detail: { element: this, entryId: this.entryId },
      }),
    );

    this.#transport = new StreamTransport({
      url,
      onChunk: (chunk) => this.#handleChunk(chunk),
      onDone: (payload) => this.#handleDone(payload),
      onError: (data) => this.#handleError(data),
      onMeta: (meta) => this.#handleMeta(meta),
    });

    try {
      this.#transport.start();
    } catch (err) {
      console.error("Unable to open stream", err);
      this.#text = "Connection failed";
      if (this.#sink) {
        this.#sink.textContent = this.#text;
      }
      this.#renderer?.renderNow(this.#text, {
        repositionTyping: false,
        shouldScroll: true,
      });
      this.#finalize({ status: "error", text: "Connection failed", reason: "stream:error" });
      return;
    }
  }

  #closeTransport() {
    if (!this.#transport) return;
    this.#transport.close();
    this.#transport = null;
  }

  #handleChunk(chunk) {
    if (this.#completed) return;

    this.#text += chunk;
    if (this.#sink) {
      this.#sink.textContent = this.#text;
    }
    const shouldEagerRender =
      this.#markdown?.dataset.rendered !== "true" && !this.#renderer?.isBusy;

    if (shouldEagerRender) {
      this.#renderer?.renderNow(this.#text, {
        repositionTyping: true,
        shouldScroll: true,
      });
      return;
    }

    this.#renderer?.scheduleRender(this.#text, {
      repositionTyping: true,
      shouldScroll: true,
    });
  }

  #handleDone(payload) {
    if (this.#completed) return;

    const assistantEntryId = payload?.assistant_entry_id || payload?.assistantEntryId;

    if (assistantEntryId) {
      this.dataset.assistantEntryId = assistantEntryId;
      this.#syncDeleteButton();
    }

    this.#renderer?.renderNow(this.#text, {
      repositionTyping: false,
      shouldScroll: true,
    });
    this.#finalize({
      status: "done",
      assistantEntryId,
      reason: "stream:complete",
    });
    this.#ensureInlineTimestamp();
  }

  #handleError(data) {
    if (this.#completed) return;

    const trimmed = data.trim();
    const hasExistingText = Boolean(this.#text && this.#text.trim());
    const errorText = trimmed ? trimmed : hasExistingText ? this.#text : FALLBACK_ERROR_MESSAGE;

    if (!hasExistingText || errorText !== this.#text) {
      this.#text = errorText;
      if (this.#sink) {
        this.#sink.textContent = this.#text;
      }
    }

    this.#renderer?.renderNow(this.#text, {
      repositionTyping: false,
      shouldScroll: true,
    });
    this.#markAsError();
    this.#finalize({ status: "error", text: errorText, reason: "stream:error" });
    this.#ensureInlineTimestamp();
  }

  #handleMeta(meta) {
    this.#meta = meta;
    if (meta.partial) {
      this.#wasPartial = true;
    }

    if (meta.repeat_guard) {
      this.#showRepeatGuardIndicator();
    } else {
      this.#clearRepeatGuardIndicator();
    }

    this.dispatchEvent(
      new CustomEvent("response-stream:meta", {
        bubbles: true,
        composed: true,
        detail: {
          element: this,
          meta,
          entryId: this.entryId,
        },
      }),
    );
  }

  #ensureInlineTimestamp() {
    const container = this.querySelector(".entry-actions-inline");
    if (!container) return;
    if (container.querySelector(".entry-time")) return;

    const now = new Date();
    const timeEl = document.createElement("time");
    timeEl.className = "entry-time";
    timeEl.dateTime = now.toISOString();
    timeEl.dataset.timeRaw = now.toISOString();
    timeEl.title = formatLocalTimestamp(now);
    timeEl.textContent = formatLocalTime(now);

    const kindIndicator = container.querySelector(".entry-kind-indicator");
    if (kindIndicator) {
      container.insertBefore(timeEl, kindIndicator);
      return;
    }
    const deleteBtn = container.querySelector(".entry-delete");
    if (deleteBtn) {
      container.insertBefore(timeEl, deleteBtn);
      return;
    }
    container.appendChild(timeEl);
  }

  #finalize({ status, assistantEntryId = null, text = "", reason = null }) {
    if (this.#completed) return;
    this.#completed = true;

    this.dataset.streaming = "false";
    this.removeAttribute("data-sse-url");
    this.removeAttribute("aria-busy");

    this.#renderer?.cancelRender();
    this.#closeTransport();

    const typing = this.#typingIndicator;
    if (typing) {
      const shouldStopAnimate = status === "aborted" || (status === "done" && this.#wasPartial);
      if (shouldStopAnimate) {
        typing.classList.add("stopped");
        window.setTimeout(() => typing.remove(), 1000);
      } else {
        typing.remove();
      }
      this.#typingIndicator = null;
    }

    if (status === "error") {
      this.#markAsError();
    }

    const placeholder = this.querySelector(".entry-tags-placeholder");
    placeholder?.remove();

    const controller = this.#controller || this.#getStreamController();
    if (controller && typeof controller.notifyStreamComplete === "function") {
      controller.notifyStreamComplete(this, {
        status,
        reason,
        entryId: this.entryId,
      });
    }

    this.dispatchEvent(
      new CustomEvent("response-stream:complete", {
        bubbles: true,
        composed: true,
        detail: {
          element: this,
          status,
          assistantEntryId,
          text,
          meta: this.#meta,
          entryId: this.entryId,
        },
      }),
    );

    const htmxRef = (typeof window !== "undefined" && window.htmx) || null;
    if (htmxRef?.ajax && this.entryId) {
      htmxRef.ajax("GET", `/e/actions/${this.entryId}`, { swap: "none" });
    }
  }

  #syncDeleteButton() {
    const button = this.#deleteButton;
    if (!button) {
      return;
    }

    const msgId = this.dataset?.assistantEntryId || null;
    const template = button.dataset?.deleteTemplate || "";
    const placeholder = button.dataset?.deletePlaceholder || "";
    const targetTemplate = button.dataset?.targetTemplate || "";

    if (!msgId || !template || !placeholder) {
      button.disabled = true;
      button.dataset.ready = "false";
      button.removeAttribute("hx-delete");
      button.removeAttribute("hx-target");
      return;
    }

    this.id = `entry-${msgId}`;
    const deleteUrl = template.replace(placeholder, msgId);
    button.setAttribute("hx-delete", deleteUrl);
    if (targetTemplate) {
      button.setAttribute("hx-target", targetTemplate.replace(placeholder, msgId));
    }
    button.disabled = false;
    button.dataset.ready = "true";
    if (window.htmx?.process) {
      window.htmx.process(button);
    }
  }

  #showRepeatGuardIndicator() {
    this.dataset.repeatGuard = "true";

    const indicator = this.#ensureRepeatGuardIndicator();
    if (!indicator) {
      return;
    }

    indicator.classList.remove("repeat-guard-indicator--leaving");
    indicator.classList.add("repeat-guard-indicator--visible");

    const reduceMotion = isMotionReduced();
    if (this.#repeatGuardWavesDismissed) {
      indicator.classList.add("repeat-guard-indicator--calm");
      return;
    }

    indicator.classList.remove("repeat-guard-indicator--calm");

    if (reduceMotion) {
      this.#calmRepeatGuardIndicator();
      return;
    }

    indicator.classList.remove("repeat-guard-indicator--visible");
    void indicator.offsetWidth;
    indicator.classList.add("repeat-guard-indicator--visible");
    this.#scheduleRepeatGuardCalming();
  }

  #scheduleRepeatGuardCalming() {
    if (this.#repeatGuardWavesDismissed) {
      return;
    }

    if (this.#repeatGuardHideTimer) {
      clearTimeout(this.#repeatGuardHideTimer);
    }

    this.#repeatGuardHideTimer = window.setTimeout(() => {
      this.#repeatGuardHideTimer = null;
      this.#calmRepeatGuardIndicator();
    }, REPEAT_GUARD_HIDE_DELAY_MS);
  }

  #calmRepeatGuardIndicator() {
    if (this.#repeatGuardWavesDismissed) {
      return;
    }

    const indicator = this.#repeatGuardIndicator;
    if (!indicator) {
      return;
    }

    this.#repeatGuardWavesDismissed = true;
    indicator.classList.add("repeat-guard-indicator--calm");
  }

  #clearRepeatGuardIndicator({ immediate = false } = {}) {
    delete this.dataset.repeatGuard;

    if (this.#repeatGuardHideTimer) {
      clearTimeout(this.#repeatGuardHideTimer);
      this.#repeatGuardHideTimer = null;
    }

    this.#repeatGuardWavesDismissed = false;

    const indicator = this.#repeatGuardIndicator;
    if (!indicator) {
      return;
    }

    this.#repeatGuardIndicator = null;

    if (!indicator.isConnected) {
      return;
    }

    const reduceMotion = isMotionReduced();
    if (reduceMotion || immediate) {
      indicator.remove();
      return;
    }

    indicator.classList.remove("repeat-guard-indicator--visible");
    indicator.classList.add("repeat-guard-indicator--leaving");
    indicator.addEventListener("transitionend", () => indicator.remove(), { once: true });
  }

  #ensureRepeatGuardIndicator() {
    if (this.#repeatGuardIndicator?.isConnected) {
      return this.#repeatGuardIndicator;
    }

    const indicator = this.#repeatGuardIndicator || this.#createRepeatGuardIndicator();

    if (!indicator) {
      return null;
    }

    if (!indicator.isConnected) {
      this.appendChild(indicator);
    }

    this.#repeatGuardIndicator = indicator;
    return indicator;
  }

  #createRepeatGuardIndicator() {
    const indicator = document.createElement("div");
    indicator.className = "repeat-guard-indicator";
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");
    indicator.title = REPEAT_GUARD_DESCRIPTION;

    const waves = document.createElement("span");
    waves.className = "repeat-guard-indicator__waves";
    waves.setAttribute("aria-hidden", "true");

    const primaryDot = document.createElement("span");
    primaryDot.className = "repeat-guard-indicator__dot";

    const delayedDot = document.createElement("span");
    delayedDot.className = "repeat-guard-indicator__dot repeat-guard-indicator__dot--delay";

    const lateDot = document.createElement("span");
    lateDot.className = "repeat-guard-indicator__dot repeat-guard-indicator__dot--late";

    waves.append(primaryDot, delayedDot, lateDot);

    const label = document.createElement("span");
    label.className = "repeat-guard-indicator__label";
    label.textContent = REPEAT_GUARD_BADGE;

    const srOnly = document.createElement("span");
    srOnly.className = "visually-hidden";
    srOnly.textContent = REPEAT_GUARD_DESCRIPTION;

    indicator.append(waves, label, srOnly);
    return indicator;
  }

  #markAsError() {
    this.dataset.error = "true";
    this.classList.add("entry--error");
  }
}

if (!customElements.get("response-stream")) {
  customElements.define("response-stream", ResponseStreamElement);
}
