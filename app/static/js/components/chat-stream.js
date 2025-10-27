import { renderMarkdown } from "../markdown.js";
import { positionTypingIndicator } from "../typing-indicator.js";
import { createListenerBag } from "../utils/events.js";
import { IncrementalMarkdownRenderer } from "../chat/incremental-markdown-renderer.js";

const TYPING_INDICATOR_SELECTOR = "#typing-indicator";

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

class ChatStreamElement extends HTMLElement {
  #state = null;
  #setStreaming = () => {};
  #scrollToBottom = () => {};
  #sseRenders = new WeakMap();
  #pendingTyping = new WeakMap();
  #renderers = new WeakMap();
  #listeners = null;
  #connected = false;
  #boundHandleMessage;

  constructor() {
    super();
    this.#boundHandleMessage = (event) => this.#handleMessage(event);
  }

  connectedCallback() {
    this.#connected = true;
    this.#init();
  }

  disconnectedCallback() {
    this.#connected = false;
    this.#destroy();
  }

  set state(value) {
    this.#state = value || null;
  }

  set setStreaming(value) {
    this.#setStreaming = typeof value === "function" ? value : () => {};
  }

  set scrollToBottom(value) {
    this.#scrollToBottom = typeof value === "function" ? value : () => {};
  }

  #init() {
    if (!this.#connected) return;
    this.#destroy();
    this.#listeners = createListenerBag();
    this.#listeners.add(this, "htmx:sseMessage", this.#boundHandleMessage);
  }

  #destroy() {
    this.#listeners?.abort();
    this.#listeners = null;
    this.#sseRenders = new WeakMap();
    this.#pendingTyping = new WeakMap();
    this.#renderers = new WeakMap();
  }

  #handleMessage(evt) {
    const { type } = evt.detail || {};
    const wrap = evt.target.closest(".assistant-stream");
    if (!wrap) return;

    const sink = wrap.querySelector(".raw-response");
    const contentDiv = wrap.querySelector(".markdown-body");
    if (!sink || !contentDiv) return;

    const renderNow = (shouldReposition = true) => {
      const typing = wrap.querySelector(TYPING_INDICATOR_SELECTOR);
      const text = (sink.textContent || "").replace(/\[newline\]/g, "\n");

      if (typing?.parentNode) {
        typing.parentNode.removeChild(typing);
      }

      const renderer = this.#getRenderer(contentDiv);
      const html = renderMarkdown(text);
      const changed = renderer.update(html);
      contentDiv.dataset.rendered = "true";

      this.#pendingTyping.delete(contentDiv);

      if (typing && shouldReposition) {
        this.#pendingTyping.set(contentDiv, {
          typing,
          shouldScroll: true,
        });
        this.handleMarkdownRendered(contentDiv);
        return { hadTyping: true, changed };
      }
      return { hadTyping: false, changed };
    };

    const scheduleRender = (fn) => {
      const prev = this.#sseRenders.get(wrap);
      if (prev) cancelAnimationFrame(prev);
      const id = requestAnimationFrame(() => {
        this.#sseRenders.delete(wrap);
        fn();
      });
      this.#sseRenders.set(wrap, id);
    };

    if (type === "message") {
      scheduleRender(() => {
        const { hadTyping, changed } = renderNow(true);
        if (changed && !hadTyping) {
          this.#scrollToBottom();
        }
      });
      return;
    }

    if (type === "error" || type === "done") {
      const rid = this.#sseRenders.get(wrap);
      if (rid) {
        cancelAnimationFrame(rid);
        this.#sseRenders.delete(wrap);
      }
      const { hadTyping, changed } = renderNow(false);

      const indicator = wrap.querySelector(TYPING_INDICATOR_SELECTOR);
      if (indicator && !indicator.classList.contains("stopped")) {
        indicator.remove();
      }
      wrap.removeAttribute("hx-ext");
      wrap.removeAttribute("sse-connect");
      wrap.removeAttribute("sse-close");
      if (this.#state) {
        this.#state.currentStreamMsgId = null;
      }
      this.#setStreaming(false);

      if (type !== "error") {
        this.#loadMetaChips(evt, wrap);
      } else {
        const placeholder = wrap.querySelector(".meta-chips-placeholder");
        if (placeholder) {
          placeholder.remove();
        }
      }
      if (changed && !hadTyping) {
        this.#scrollToBottom();
      }
    }
  }

  handleMarkdownRendered(el) {
    if (!el) return;

    const pending = this.#pendingTyping.get(el);
    if (!pending) return;

    this.#pendingTyping.delete(el);

    const { typing, shouldScroll } = pending;
    if (typing) {
      positionTypingIndicator(el, typing);
    }

    if (shouldScroll) {
      this.#scrollToBottom();
    }
  }

  #loadMetaChips(evt, wrap) {
    try {
      const data = JSON.parse(evt.detail?.data || "{}");
      const assistantId = data.assistant_msg_id;
      if (assistantId) {
        wrap.dataset.assistantMsgId = assistantId;
        const placeholder = wrap.querySelector(".meta-chips-placeholder");
        if (placeholder) {
          wrap.addEventListener(
            "htmx:afterSwap",
            (e) => {
              if (e.target.classList?.contains("meta-chips")) {
                revealMetaChips(e.target, this.#scrollToBottom);
              }
            },
            { once: true }
          );
          htmx.ajax("GET", `/c/meta-chips/${assistantId}`, {
            target: placeholder,
            swap: "outerHTML",
          });
        }
      }
    } catch (err) {
      console.error("failed to load meta chips", err);
    }
  }

  #getRenderer(target) {
    if (!target) {
      return {
        update: () => false,
        reset: () => {},
      };
    }
    let renderer = this.#renderers.get(target);
    if (!renderer) {
      renderer = new IncrementalMarkdownRenderer(target);
      this.#renderers.set(target, renderer);
    }
    return renderer;
  }
}

if (!customElements.get("chat-stream")) {
  customElements.define("chat-stream", ChatStreamElement);
}
