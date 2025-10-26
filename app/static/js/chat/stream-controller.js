import { renderMarkdown } from "../markdown.js";
import { positionTypingIndicator } from "../typing-indicator.js";

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

export class StreamController {
  constructor({ chat, state, setStreaming, scrollToBottom }) {
    this.chat = chat;
    this.state = state;
    this.setStreaming = setStreaming || (() => {});
    this.scrollToBottom = scrollToBottom || (() => {});

    this.sseRenders = new WeakMap();
    this.listener = null;
  }

  init() {
    this.destroy();
    this.listener = (evt) => this.handleMessage(evt);
    document.body.addEventListener("htmx:sseMessage", this.listener);
  }

  destroy() {
    if (this.listener) {
      document.body.removeEventListener("htmx:sseMessage", this.listener);
      this.listener = null;
    }
    this.sseRenders = new WeakMap();
  }

  handleMessage(evt) {
    const { type } = evt.detail;
    const wrap = evt.target.closest(".assistant-stream");
    if (!wrap) return;

    const sink = wrap.querySelector(".raw-response");
    const contentDiv = wrap.querySelector(".markdown-body");
    if (!sink || !contentDiv) return;

    const renderNow = () => {
      let text = (sink.textContent || "").replace(/\[newline\]/g, "\n");
      const typing = wrap.querySelector(TYPING_INDICATOR_SELECTOR);
      contentDiv.innerHTML = renderMarkdown(text);
      contentDiv.dataset.rendered = "true";
      if (typing) {
        positionTypingIndicator(contentDiv, typing);
      }
    };

    const scheduleRender = (fn) => {
      const prev = this.sseRenders.get(wrap);
      if (prev) cancelAnimationFrame(prev);
      const id = requestAnimationFrame(() => {
        this.sseRenders.delete(wrap);
        fn();
      });
      this.sseRenders.set(wrap, id);
    };

    if (type === "message") {
      scheduleRender(() => {
        renderNow();
        this.scrollToBottom();
      });
      return;
    }

    if (type === "error" || type === "done") {
      const rid = this.sseRenders.get(wrap);
      if (rid) {
        cancelAnimationFrame(rid);
        this.sseRenders.delete(wrap);
      }
      renderNow();

      const indicator = wrap.querySelector(TYPING_INDICATOR_SELECTOR);
      if (indicator && !indicator.classList.contains("stopped")) {
        indicator.remove();
      }
      wrap.removeAttribute("hx-ext");
      wrap.removeAttribute("sse-connect");
      wrap.removeAttribute("sse-close");
      this.state.currentStreamMsgId = null;
      this.setStreaming(false);

      if (type !== "error") {
        this.loadMetaChips(evt, wrap);
      } else {
        const placeholder = wrap.querySelector(".meta-chips-placeholder");
        if (placeholder) {
          placeholder.remove();
        }
      }
      this.scrollToBottom();
    }
  }

  loadMetaChips(evt, wrap) {
    try {
      const data = JSON.parse(evt.detail.data || "{}");
      const assistantId = data.assistant_msg_id;
      if (assistantId) {
        wrap.dataset.assistantMsgId = assistantId;
        const placeholder = wrap.querySelector(".meta-chips-placeholder");
        if (placeholder) {
          wrap.addEventListener(
            "htmx:afterSwap",
            (e) => {
              if (e.target.classList?.contains("meta-chips")) {
                revealMetaChips(e.target, this.scrollToBottom);
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
}
