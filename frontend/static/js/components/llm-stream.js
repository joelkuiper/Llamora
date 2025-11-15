import { renderMarkdown } from "../markdown.js";
import {
  positionTypingIndicator,
  TYPING_INDICATOR_SELECTOR,
} from "../typing-indicator.js";
import { IncrementalMarkdownRenderer } from "../chat/incremental-markdown-renderer.js";
import { requestScrollForceBottom } from "../chat/scroll-manager.js";
import { prefersReducedMotion } from "../utils/motion.js";
import {
  applyTimezoneSearchParam,
  buildTimezoneQueryParam,
  getTimezone,
  TIMEZONE_QUERY_PARAM,
} from "../utils/timezone-service.js";
import { scheduleFrame } from "../utils/scheduler.js";

const NEWLINE_REGEX = /\[newline\]/g;
const RENDER_COOLDOWN_MS = 16;
const FALLBACK_ERROR_MESSAGE = "The assistant ran into an error. Please try again.";
const REPEAT_GUARD_BADGE = "response trimmed";
const REPEAT_GUARD_DESCRIPTION = "Response paused after repeating itself.";
const REPEAT_GUARD_HIDE_DELAY_MS = 5000;
const ESCAPED_TIMEZONE_PARAM = TIMEZONE_QUERY_PARAM.replace(
  /[.*+?^${}()|[\]\\]/g,
  "\\$&"
);
const TIMEZONE_QUERY_PARAM_PATTERN = new RegExp(
  `[?&]${ESCAPED_TIMEZONE_PARAM}=`
);

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
  requestScrollForceBottom({ source: "llm-stream", ...detail });
}

function revealMetaChips(container) {
  if (!container || !container.hidden) return;

  const parent = container.closest(".message");
  const reduceMotion = prefersReducedMotion();
  const start = reduceMotion ? undefined : parent?.offsetHeight;
  container.hidden = false;

  if (reduceMotion) {
    requestScrollToBottom({ reason: "meta" });
    return;
  }

  const end = parent?.offsetHeight;

  if (parent && start !== undefined && end !== undefined) {
    parent.style.height = start + "px";
    parent.offsetHeight;
    parent.style.transition = "height 0.2s ease";
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

  container.classList.add("chip-enter");
  container.addEventListener(
    "animationend",
    () => {
      container.classList.remove("chip-enter");
      requestScrollToBottom({ reason: "meta" });
    },
    { once: true }
  );
}

class LlmStreamElement extends HTMLElement {
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
  #repeatGuardIndicator = null;
  #repeatGuardHideTimer = null;
  #repeatGuardWavesDismissed = false;
  #boundHandleMessage;
  #boundHandleDone;
  #boundHandleError;
  #boundHandleMeta;

  constructor() {
    super();
    this.#boundHandleMessage = (event) => this.#handleMessage(event);
    this.#boundHandleDone = (event) => this.#handleDone(event);
    this.#boundHandleError = (event) => this.#handleError(event);
    this.#boundHandleMeta = (event) => this.#handleMeta(event);
  }

  #getStreamingSession() {
    const host = this.closest?.("chat-view");
    if (host && "streamingSession" in host) {
      return host.streamingSession || null;
    }
    return null;
  }

  connectedCallback() {
    this.#sink = this.querySelector(".raw-response");
    this.#markdown = this.querySelector(".markdown-body");
    this.#typingIndicator = this.querySelector(TYPING_INDICATOR_SELECTOR) || null;
    this.#repeatGuardIndicator =
      this.querySelector(".repeat-guard-indicator") || null;
    this.#repeatGuardWavesDismissed = Boolean(
      this.#repeatGuardIndicator?.classList.contains("repeat-guard-indicator--calm")
    );

    if (this.#sink) {
      this.#text = decodeChunk(this.#sink.textContent || "");
    }

    if (this.#markdown && !this.#renderer) {
      this.#renderer = new IncrementalMarkdownRenderer(this.#markdown);
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
  }

  get userMsgId() {
    return this.dataset.userMsgId || null;
  }

  get sseUrl() {
    return this.dataset.sseUrl || this.getAttribute("sse-url") || "";
  }

  abort() {
    if (this.#completed) return;
    const session = this.#getStreamingSession();
    session?.abort();
    this.#finalize({ status: "aborted" });
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

    const zone = getTimezone();

    let url = this.sseUrl;
    try {
      const base = window.location?.origin || undefined;
      const parsed = new URL(url, base);
      applyTimezoneSearchParam(parsed.searchParams, zone);
      url = `${parsed.pathname}${parsed.search}`;
    } catch (err) {
      if (!TIMEZONE_QUERY_PARAM_PATTERN.test(url)) {
        const separator = url.includes("?") ? "&" : "?";
        url = `${url}${separator}${buildTimezoneQueryParam(zone)}`;
      }
    }

    this.dataset.sseUrl = url;

    this.dataset.streaming = "true";
    this.setAttribute("aria-busy", "true");
    this.#meta = null;
    const session = this.#getStreamingSession();
    if (session && this.userMsgId) {
      session.begin(this.userMsgId);
    }
    this.dispatchEvent(
      new CustomEvent("llm-stream:start", {
        bubbles: true,
        composed: true,
        detail: { element: this, userMsgId: this.userMsgId },
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
      this.#finalize({ status: "error", message: "Connection failed" });
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
    const assistantMsgId = payload?.assistant_msg_id || payload?.assistantMsgId;

    if (assistantMsgId) {
      this.dataset.assistantMsgId = assistantMsgId;
    }

    this.#renderNow({ repositionTyping: false, shouldScroll: true });
    this.#finalize({ status: "done", assistantMsgId });
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
    this.#finalize({ status: "error", message });
  }

  #handleMeta(event) {
    const raw = decodeChunk(event?.data || "");
    if (!raw) return;

    const meta = parseMetaPayload(raw);
    if (!meta) return;

    this.#meta = meta;

    if (meta.repeat_guard) {
      this.#showRepeatGuardIndicator();
    } else {
      this.#clearRepeatGuardIndicator();
    }

    this.dispatchEvent(
      new CustomEvent("llm-stream:meta", {
        bubbles: true,
        composed: true,
        detail: {
          element: this,
          meta,
          userMsgId: this.userMsgId,
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

  #finalize({ status, assistantMsgId = null, message = "" }) {
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
      if (status === "aborted") {
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

    if (status === "done" && assistantMsgId) {
      this.#loadMetaChips(assistantMsgId);
    } else {
      const placeholder = this.querySelector(".meta-chips-placeholder");
      placeholder?.remove();
    }

    const session = this.#getStreamingSession();
    if (session) {
      session.complete(status);
    }

    this.dispatchEvent(
      new CustomEvent("llm-stream:complete", {
        bubbles: true,
        composed: true,
        detail: {
          element: this,
          status,
          assistantMsgId,
          message,
          meta: this.#meta,
          userMsgId: this.userMsgId,
        },
      })
    );
  }

  #showRepeatGuardIndicator() {
    this.dataset.repeatGuard = "true";

    const indicator = this.#ensureRepeatGuardIndicator();
    if (!indicator) {
      return;
    }

    indicator.classList.remove("repeat-guard-indicator--leaving");
    indicator.classList.add("repeat-guard-indicator--visible");

    const reduceMotion = prefersReducedMotion();
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

    const reduceMotion = prefersReducedMotion();
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

    try {
      this.addEventListener(
        "htmx:afterSwap",
        (event) => {
          if (event.target?.classList?.contains("meta-chips")) {
            revealMetaChips(event.target);
          }
        },
        { once: true }
      );

      htmx.ajax("GET", `/c/meta-chips/${assistantId}`, {
        target: placeholder,
        swap: "outerHTML",
      });
    } catch (err) {
      console.error("failed to load meta chips", err);
    }
  }

  #markAsError() {
    this.dataset.error = "true";
    this.classList.add("message--error");
  }
}

if (!customElements.get("llm-stream")) {
  customElements.define("llm-stream", LlmStreamElement);
}
