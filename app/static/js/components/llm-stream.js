import { renderMarkdown } from "../markdown.js";
import { positionTypingIndicator } from "../typing-indicator.js";
import { IncrementalMarkdownRenderer } from "../chat/incremental-markdown-renderer.js";

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

function revealMetaChips(container, scrollToBottom) {
  if (!container || !container.hidden) return;

  const parent = container.closest(".message");
  const start = parent?.offsetHeight;
  container.hidden = false;
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
      scrollToBottom();
    },
    { once: true }
  );
}

class LlmStreamElement extends HTMLElement {
  #eventSource = null;
  #renderer = null;
  #scrollToBottom = () => {};
  #renderFrame = null;
  #pendingTyping = null;
  #completed = false;
  #text = "";
  #sink = null;
  #markdown = null;
  #typingIndicator = null;
  #boundHandleMessage;
  #boundHandleDone;
  #boundHandleError;

  constructor() {
    super();
    this.#boundHandleMessage = (event) => this.#handleMessage(event);
    this.#boundHandleDone = (event) => this.#handleDone(event);
    this.#boundHandleError = (event) => this.#handleError(event);
  }

  connectedCallback() {
    this.#sink = this.querySelector(".raw-response");
    this.#markdown = this.querySelector(".markdown-body");
    this.#typingIndicator = this.querySelector("#typing-indicator") || null;

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
  }

  set scrollToBottom(fn) {
    this.#scrollToBottom = typeof fn === "function" ? fn : () => {};
  }

  get userMsgId() {
    return this.dataset.userMsgId || null;
  }

  get sseUrl() {
    return this.dataset.sseUrl || this.getAttribute("sse-url") || "";
  }

  abort() {
    if (this.#completed) return;
    this.#finalize({ status: "aborted" });
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
      this.#scrollToBottom();
    }
  }

  #startStream() {
    if (this.#eventSource || !this.sseUrl) return;

    this.dataset.streaming = "true";
    this.setAttribute("aria-busy", "true");
    this.dispatchEvent(
      new CustomEvent("llm-stream:start", {
        bubbles: true,
        composed: true,
        detail: { element: this, userMsgId: this.userMsgId },
      })
    );

    try {
      this.#eventSource = new EventSource(this.sseUrl, { withCredentials: true });
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
  }

  #closeEventSource() {
    if (!this.#eventSource) return;
    this.#eventSource.removeEventListener("message", this.#boundHandleMessage);
    this.#eventSource.removeEventListener("done", this.#boundHandleDone);
    this.#eventSource.removeEventListener("error", this.#boundHandleError);
    this.#eventSource.close();
    this.#eventSource = null;
  }

  #cancelRender() {
    if (!this.#renderFrame) return;
    cancelAnimationFrame(this.#renderFrame);
    this.#renderFrame = null;
  }

  #handleMessage(event) {
    if (this.#completed) return;
    const chunk = decodeChunk(event?.data || "");
    if (!chunk) return;

    this.#text += chunk;
    if (this.#sink) {
      this.#sink.textContent = this.#text;
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
    if (data) {
      this.#text = data;
      if (this.#sink) {
        this.#sink.textContent = this.#text;
      }
    }

    this.#renderNow({ repositionTyping: false, shouldScroll: true });
    this.#finalize({ status: "error", message: data || "" });
  }

  #scheduleRender({ repositionTyping = false, shouldScroll = false } = {}) {
    this.#cancelRender();
    this.#renderFrame = requestAnimationFrame(() => {
      this.#renderFrame = null;
      this.#renderNow({ repositionTyping, shouldScroll });
    });
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

    this.#markdown.dataset.markdownSource = this.#text || "";
    this.#markdown.dataset.rendered = "true";

    if (reposition) {
      this.#pendingTyping = reposition;
      this.handleMarkdownRendered(this.#markdown);
    } else if (shouldScroll && changed) {
      this.#scrollToBottom();
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

    if (status === "done" && assistantMsgId) {
      this.#loadMetaChips(assistantMsgId);
    } else {
      const placeholder = this.querySelector(".meta-chips-placeholder");
      placeholder?.remove();
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
          userMsgId: this.userMsgId,
        },
      })
    );
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
            revealMetaChips(event.target, () => this.#scrollToBottom());
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
}

if (!customElements.get("llm-stream")) {
  customElements.define("llm-stream", LlmStreamElement);
}
