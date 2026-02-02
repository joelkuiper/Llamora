import { renderMarkdown } from "../markdown.js";
import {
  positionTypingIndicator,
  TYPING_INDICATOR_SELECTOR,
} from "../typing-indicator.js";
import { IncrementalMarkdownRenderer } from "../entries/incremental-markdown-renderer.js";
import { requestScrollForceBottom } from "../entries/scroll-manager.js";
import { animateMotion, isMotionReduced } from "../services/motion.js";
import { applyTimezoneQuery, formatLocalTime, formatLocalTimestamp } from "../services/time.js";
import { scheduleFrame } from "../utils/scheduler.js";

const NEWLINE_REGEX = /\[newline\]/g;
const RENDER_COOLDOWN_MS = 16;
const FALLBACK_ERROR_MESSAGE = "The assistant ran into an error. Please try again.";
const REPEAT_GUARD_BADGE = "response trimmed";
const REPEAT_GUARD_DESCRIPTION = "Response paused after repeating itself.";
const REPEAT_GUARD_HIDE_DELAY_MS = 5000;
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

function requestScrollToBottom(detail = {}) {
  requestScrollForceBottom({ source: "response-stream", ...detail });
}

function revealMetaChips(container) {
  if (!container || !container.hidden) return;

  const parent = container.closest(".message");
  const reduceMotion = isMotionReduced();
  const start = reduceMotion ? undefined : parent?.offsetHeight;
  container.hidden = false;

  if (!reduceMotion) {
    const end = parent?.offsetHeight;

    if (parent && start !== undefined && end !== undefined) {
      parent.style.height = start + "px";
      parent.offsetHeight;
      parent.style.transition = "height var(--motion-gentle, 0.2s) ease";
      parent.style.height = end + "px";
      parent.addEventListener(
        "transitionend",
        () => {
          parent.style.height = "";
          parent.style.transition = "";
        },
        { once: true }
      );
    }
  }

  const notifyScroll = () => {
    requestScrollToBottom({ reason: "meta" });
  };

  animateMotion(container, "motion-animate-chip-enter", {
    onFinish: notifyScroll,
    onCancel: notifyScroll,
    reducedMotion: (_, done) => {
      done();
    },
  });
}

class ResponseStreamElement extends HTMLElement {
  #eventSource = null;
  #renderer = null;
  #renderFrame = null;
  #renderCooldownTimer = null;
  #pendingRenderOptions = null;
  #pendingTyping = null;
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
  #boundHandleMessage;
  #boundHandleDone;
  #boundHandleError;
  #boundHandleMeta;
  #controller = null;
  #controllerDisconnect = null;
  #metaChipsRequest = null;
  #metaChipsAssistantId = null;
  #metaChipsListenerController = null;
  #deleteButton = null;
  #untracked = false;

  constructor() {
    super();
    this.#boundHandleMessage = (event) => this.#handleMessage(event);
    this.#boundHandleDone = (event) => this.#handleDone(event);
    this.#boundHandleError = (event) => this.#handleError(event);
    this.#boundHandleMeta = (event) => this.#handleMeta(event);
  }

  #getStreamingSession() {
    const host = this.closest?.("entry-view");
    if (host && "streamingSession" in host) {
      return host.streamingSession || null;
    }
    return null;
  }

  #getStreamController() {
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

    if (this.#controllerDisconnect) {
      this.#controllerDisconnect();
      this.#controllerDisconnect = null;
    }

    this.#controller = controller || null;

    if (this.#controller && typeof this.#controller.registerStream === "function") {
      const cleanup = this.#controller.registerStream(this);
      if (typeof cleanup === "function") {
        this.#controllerDisconnect = cleanup;
      }
    }
  }

  connectedCallback() {
    this.#sink = this.querySelector(".raw-response");
    this.#markdown = this.querySelector(".markdown-body");
    this.#typingIndicator = this.querySelector(TYPING_INDICATOR_SELECTOR) || null;
    this.#repeatGuardIndicator =
      this.querySelector(".repeat-guard-indicator") || null;
    this.#deleteButton = this.querySelector(".entry-delete") || null;
    this.#repeatGuardWavesDismissed = Boolean(
      this.#repeatGuardIndicator?.classList.contains("repeat-guard-indicator--calm")
    );

    if (this.#sink) {
      this.#text = decodeChunk(this.#sink.textContent || "");
    }

    if (this.#markdown && !this.#renderer) {
      this.#renderer = new IncrementalMarkdownRenderer(this.#markdown);
    }

    this.#untracked =
      this.dataset?.streamKind === "opening" || this.dataset?.untracked === "true";
    if (!this.#untracked) {
      this.#syncController();
      this.#syncDeleteButton();
    }

    if (!this.#completed) {
      this.#startStream();
    }
  }

  disconnectedCallback() {
    this.#cancelRender();
    this.#closeEventSource();
    if (this.#repeatGuardHideTimer) {
      clearTimeout(this.#repeatGuardHideTimer);
      this.#repeatGuardHideTimer = null;
    }
    this.#repeatGuardWavesDismissed = false;
    this.#deleteButton = null;
    if (this.#controllerDisconnect) {
      this.#controllerDisconnect();
      this.#controllerDisconnect = null;
    }
    this.#controller = null;
    this.#cancelMetaChipsRequest();
    this.#teardownMetaChipsListener();
  }

  get entryId() {
    return this.dataset.entryId || null;
  }

  get sseUrl() {
    return this.dataset.sseUrl || this.getAttribute("sse-url") || "";
  }

  abort({ reason = "user:abort" } = {}) {
    if (this.#completed) return;
    const entryId = this.entryId;
    const controller = this.#controller || this.#getStreamController();
    let aborted = false;
    if (controller && typeof controller.notifyStreamAbort === "function") {
      aborted = controller.notifyStreamAbort(this, { reason });
    } else {
      const session = this.#getStreamingSession();
      aborted = session?.abort({ reason, entryId }) ?? false;
    }

    this.#finalize({ status: "aborted", reason });
  }

  get isStreaming() {
    return this.dataset.streaming === "true";
  }

  isStreamDormant() {
    return this.isStreaming && !this.#eventSource && !this.#completed;
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
    const pending = this.#pendingTyping;
    if (!pending) return;

    this.#pendingTyping = null;
    const { typing, shouldScroll } = pending;
    if (typing) {
      positionTypingIndicator(el, typing);
      this.#typingIndicator = typing;
    }
    if (shouldScroll) {
      requestScrollToBottom({ reason: "markdown", element: this });
    }
  }

  #startStream() {
    if (this.#eventSource || !this.sseUrl) return;

    const url = applyTimezoneQuery(this.sseUrl);

    this.dataset.sseUrl = url;

    this.dataset.streaming = "true";
    this.setAttribute("aria-busy", "true");
    this.#meta = null;
    this.#wasPartial = false;
    if (!this.#untracked) {
      const controller = this.#controller || this.#getStreamController();
      if (controller && typeof controller.notifyStreamStart === "function") {
        controller.notifyStreamStart(this, { reason: "stream:start" });
      } else {
        const session = this.#getStreamingSession();
        if (session && this.entryId) {
          session.begin(this.entryId);
        }
        requestScrollForceBottom({ source: "stream:start" });
      }
    } else {
      requestScrollForceBottom({ source: "stream:start" });
    }
    this.dispatchEvent(
      new CustomEvent("response-stream:start", {
        bubbles: true,
        composed: true,
        detail: { element: this, entryId: this.entryId },
      })
    );

    try {
      this.#eventSource = new EventSource(url, { withCredentials: true });
    } catch (err) {
      console.error("Unable to open stream", err);
      this.#text = "Connection failed";
      if (this.#sink) {
        this.#sink.textContent = this.#text;
      }
      this.#renderNow({ repositionTyping: false, shouldScroll: true });
      this.#finalize({ status: "error", message: "Connection failed", reason: "stream:error" });
      return;
    }

    this.#eventSource.addEventListener("message", this.#boundHandleMessage);
    this.#eventSource.addEventListener("done", this.#boundHandleDone);
    this.#eventSource.addEventListener("error", this.#boundHandleError);
    this.#eventSource.addEventListener("meta", this.#boundHandleMeta);
  }

  #closeEventSource() {
    if (!this.#eventSource) return;
    this.#eventSource.removeEventListener("message", this.#boundHandleMessage);
    this.#eventSource.removeEventListener("done", this.#boundHandleDone);
    this.#eventSource.removeEventListener("error", this.#boundHandleError);
    this.#eventSource.removeEventListener("meta", this.#boundHandleMeta);
    this.#eventSource.close();
    this.#eventSource = null;
  }

  #cancelRender({ clearPending = true } = {}) {
    if (this.#renderCooldownTimer) {
      clearTimeout(this.#renderCooldownTimer);
      this.#renderCooldownTimer = null;
    }

    if (this.#renderFrame) {
      this.#renderFrame.cancel?.();
      this.#renderFrame = null;
    }

    if (clearPending) {
      this.#pendingRenderOptions = null;
    }
  }

  #handleMessage(event) {
    if (this.#completed) return;
    const chunk = decodeChunk(event?.data || "");
    if (!chunk) return;

    this.#text += chunk;
    if (this.#sink) {
      this.#sink.textContent = this.#text;
    }
    const shouldEagerRender =
      this.#markdown?.dataset.rendered !== "true" &&
      !this.#renderCooldownTimer &&
      !this.#renderFrame;

    if (shouldEagerRender) {
      this.#renderNow({ repositionTyping: true, shouldScroll: true });
      return;
    }

    this.#scheduleRender({ repositionTyping: true, shouldScroll: true });
  }

  #handleDone(event) {
    if (this.#completed) return;

    const payload = parseDonePayload(event?.data || "");
    const assistantEntryId =
      payload?.assistant_entry_id || payload?.assistantEntryId;

    if (assistantEntryId) {
      this.dataset.assistantEntryId = assistantEntryId;
      this.#syncDeleteButton();
    }

    this.#renderNow({ repositionTyping: false, shouldScroll: true });
    this.#finalize({
      status: "done",
      assistantEntryId,
      reason: "stream:complete",
    });
    this.#ensureInlineTimestamp();
  }

  #handleError(event) {
    if (this.#completed) return;

    const data = decodeChunk(event?.data || "");
    const trimmed = data.trim();
    const hasExistingText = Boolean(this.#text && this.#text.trim());
    const message = trimmed
      ? trimmed
      : hasExistingText
        ? this.#text
        : FALLBACK_ERROR_MESSAGE;

    if (!hasExistingText || message !== this.#text) {
      this.#text = message;
      if (this.#sink) {
        this.#sink.textContent = this.#text;
      }
    }

    this.#renderNow({ repositionTyping: false, shouldScroll: true });
    this.#markAsError();
    this.#finalize({ status: "error", message, reason: "stream:error" });
    this.#ensureInlineTimestamp();
  }

  #handleMeta(event) {
    const raw = decodeChunk(event?.data || "");
    if (!raw) return;

    const meta = parseMetaPayload(raw);
    if (!meta) return;

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
      })
    );
  }

  #scheduleRender({ repositionTyping = false, shouldScroll = false } = {}) {
    const pending = this.#pendingRenderOptions || {
      repositionTyping: false,
      shouldScroll: false,
    };

    this.#pendingRenderOptions = {
      repositionTyping: pending.repositionTyping || repositionTyping,
      shouldScroll: pending.shouldScroll || shouldScroll,
    };

    if (this.#renderCooldownTimer) {
      return;
    }

    this.#renderCooldownTimer = window.setTimeout(() => {
      this.#renderCooldownTimer = null;
      this.#cancelRender({ clearPending: false });

      const frame = scheduleFrame(() => {
        if (this.#renderFrame !== frame) {
          return;
        }
        this.#renderFrame = null;
        const options = this.#pendingRenderOptions || {
          repositionTyping: false,
          shouldScroll: false,
        };
        this.#pendingRenderOptions = null;
        this.#renderNow(options);
      });
      this.#renderFrame = frame;
    }, RENDER_COOLDOWN_MS);
  }

  #renderNow({ repositionTyping = false, shouldScroll = false } = {}) {
    if (!this.#markdown || !this.#renderer) return false;

    const typing = this.#typingIndicator;
    let reposition = null;

    if (typing?.parentNode) {
      typing.parentNode.removeChild(typing);
      if (repositionTyping) {
        reposition = { typing, shouldScroll };
      } else {
        typing.remove();
        this.#typingIndicator = null;
      }
    }

    const html = renderMarkdown(this.#text || "");
    const changed = this.#renderer.update(html);

    if ("markdownSource" in this.#markdown.dataset) {
      delete this.#markdown.dataset.markdownSource;
    }
    this.#markdown.dataset.rendered = "true";

    if (reposition) {
      this.#pendingTyping = reposition;
      this.handleMarkdownRendered(this.#markdown);
    } else if (shouldScroll && changed) {
      requestScrollToBottom({ reason: "render", element: this });
    }

    return changed;
  }

  #ensureInlineTimestamp() {
    const container = this.querySelector(".entry-actions-inline");
    if (!container) return;
    if (container.querySelector(".entry-time")) return;

    const now = new Date();
    const timeEl = document.createElement("time");
    timeEl.className = "message-time";
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

  #finalize({
    status,
    assistantEntryId = null,
    message = "",
    reason = null,
  }) {
    if (this.#completed) return;
    this.#completed = true;

    this.dataset.streaming = "false";
    this.removeAttribute("data-sse-url");
    this.removeAttribute("aria-busy");

    this.#cancelRender();
    this.#closeEventSource();
    this.#pendingTyping = null;

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

    if (status === "done" && assistantEntryId) {
      this.#loadMetaChips(assistantEntryId);
    } else {
      const placeholder = this.querySelector(".meta-chips-placeholder");
      placeholder?.remove();
    }

    if (!this.#untracked) {
      const controller = this.#controller || this.#getStreamController();
      if (controller && typeof controller.notifyStreamComplete === "function") {
        controller.notifyStreamComplete(this, {
          status,
          reason,
          entryId: this.entryId,
        });
      } else {
        const session = this.#getStreamingSession();
        if (session) {
          session.complete({ result: status, reason, entryId: this.entryId });
        }
        if (status !== "aborted") {
          requestScrollForceBottom({ source: "stream:complete" });
        }
      }
    }

    this.dispatchEvent(
      new CustomEvent("response-stream:complete", {
        bubbles: true,
        composed: true,
        detail: {
          element: this,
          status,
          assistantEntryId,
          message,
          meta: this.#meta,
          entryId: this.entryId,
        },
      })
    );

    if (!this.#untracked) {
      const htmxRef = (typeof window !== "undefined" && window.htmx) || null;
      if (htmxRef?.ajax && this.entryId) {
        htmxRef.ajax("GET", `/e/actions/${this.entryId}`, { swap: "none" });
      }
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
    indicator.addEventListener(
      "transitionend",
      () => indicator.remove(),
      { once: true }
    );
  }

  #ensureRepeatGuardIndicator() {
    if (this.#repeatGuardIndicator?.isConnected) {
      return this.#repeatGuardIndicator;
    }

    const indicator =
      this.#repeatGuardIndicator || this.#createRepeatGuardIndicator();

    if (!indicator) {
      return null;
    }

    if (!indicator.isConnected) {
      const placeholder = this.querySelector(".meta-chips-placeholder");
      if (placeholder?.parentNode === this) {
        this.insertBefore(indicator, placeholder);
      } else {
        this.appendChild(indicator);
      }
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
    delayedDot.className =
      "repeat-guard-indicator__dot repeat-guard-indicator__dot--delay";

    const lateDot = document.createElement("span");
    lateDot.className =
      "repeat-guard-indicator__dot repeat-guard-indicator__dot--late";

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

  #loadMetaChips(assistantId) {
    if (!assistantId) return;

    const placeholder = this.querySelector(".meta-chips-placeholder");
    if (!placeholder) return;

    if (this.#metaChipsAssistantId === assistantId) {
      return;
    }

    this.#metaChipsAssistantId = assistantId;
    this.#ensureMetaChipsListener();
    this.#cancelMetaChipsRequest();

    try {
      const request = htmx.ajax("GET", `/e/meta-chips/${assistantId}`, {
        target: placeholder,
        swap: "outerHTML",
      });

      if (request) {
        this.#metaChipsRequest = request;
        const cleanup = () => {
          if (this.#metaChipsRequest === request) {
            this.#metaChipsRequest = null;
          }
        };

        if (typeof request.addEventListener === "function") {
          request.addEventListener("abort", cleanup, { once: true });
          request.addEventListener("loadend", cleanup, { once: true });
        } else if (typeof request.finally === "function") {
          request.finally(cleanup);
        } else if (typeof request.then === "function") {
          request.then(cleanup, cleanup);
        } else {
          cleanup();
        }
      }
    } catch (err) {
      console.error("failed to load meta chips", err);
    }
  }

  #cancelMetaChipsRequest() {
    if (!this.#metaChipsRequest) {
      return;
    }

    const request = this.#metaChipsRequest;
    const abortFn = typeof request.abort === "function" ? request.abort : null;
    if (abortFn) {
      try {
        abortFn.call(request);
      } catch (_) {
        // ignored
      }
    }

    this.#metaChipsRequest = null;
  }

  #ensureMetaChipsListener() {
    if (this.#metaChipsListenerController) {
      return;
    }

    const controller = new AbortController();
    this.addEventListener(
      "htmx:afterSwap",
      (event) => {
        if (event.target?.classList?.contains("meta-chips")) {
          revealMetaChips(event.target);
          this.#teardownMetaChipsListener();
        }
      },
      { signal: controller.signal }
    );

    this.#metaChipsListenerController = controller;
  }

  #teardownMetaChipsListener() {
    if (!this.#metaChipsListenerController) {
      return;
    }

    this.#metaChipsListenerController.abort();
    this.#metaChipsListenerController = null;
  }

  #markAsError() {
    this.dataset.error = "true";
    this.classList.add("entry--error");
  }
}

if (!customElements.get("response-stream")) {
  customElements.define("response-stream", ResponseStreamElement);
}
