import { renderMarkdown } from "../markdown.js";
import { positionTypingIndicator } from "../typing-indicator.js";
import { scheduleFrame } from "../utils/scheduler.js";
import { IncrementalMarkdownRenderer } from "./entries-view/incremental-markdown-renderer.js";

const RENDER_COOLDOWN_MS = 16;

export class StreamRenderer {
  #markdown = null;
  #typingIndicator = null;
  #renderer = null;
  #renderFrame = null;
  #renderCooldownTimer = null;
  #pendingRenderOptions = null;
  #pendingText = "";
  #requestScroll = null;

  constructor({ markdown, typingIndicator, requestScroll }) {
    this.#markdown = markdown;
    this.#typingIndicator = typingIndicator;
    this.#requestScroll = requestScroll || null;
    if (this.#markdown) {
      this.#renderer = new IncrementalMarkdownRenderer(this.#markdown);
    }
  }

  setTypingIndicator(node) {
    this.#typingIndicator = node || null;
  }

  get isBusy() {
    return Boolean(this.#renderCooldownTimer || this.#renderFrame);
  }

  cancelRender({ clearPending = true } = {}) {
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
      this.#pendingText = "";
    }
  }

  handleMarkdownRendered(el) {
    if (!el || el !== this.#markdown) return;
    const typing = this.#typingIndicator;
    if (typing) {
      positionTypingIndicator(el, typing);
      this.#typingIndicator = typing;
    }
  }

  renderNow(text, { repositionTyping = false, shouldScroll = false } = {}) {
    if (!this.#markdown || !this.#renderer) return false;

    const typing = this.#typingIndicator;
    let placeholder = null;

    if (typing?.parentNode && repositionTyping) {
      placeholder = document.createElement("span");
      placeholder.className = "typing-indicator-placeholder";
      placeholder.textContent = "❚";
      typing.parentNode.insertBefore(placeholder, typing);
      typing.parentNode.removeChild(typing);
    }

    const html = renderMarkdown(text || "");
    const changed = this.#renderer.update(html);
    if ("markdownSource" in this.#markdown.dataset) {
      delete this.#markdown.dataset.markdownSource;
    }
    this.#markdown.dataset.rendered = "true";

    if (placeholder?.parentNode) {
      placeholder.parentNode.removeChild(placeholder);
    }

    if (typing && repositionTyping) {
      positionTypingIndicator(this.#markdown, typing);
      this.#typingIndicator = typing;
      if (shouldScroll) {
        this.#requestScroll?.({ reason: "render" });
      }
    } else if (shouldScroll && changed) {
      this.#requestScroll?.({ reason: "render" });
    }

    return changed;
  }

  scheduleRender(text, { repositionTyping = false, shouldScroll = false } = {}) {
    this.#pendingText = text || "";
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
      this.cancelRender({ clearPending: false });

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
        this.renderNow(this.#pendingText, options);
      });
      this.#renderFrame = frame;
    }, RENDER_COOLDOWN_MS);
  }
}
