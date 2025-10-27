import { createListenerBag } from "../utils/events.js";

export class ChatFormController {
  constructor({
    root = document,
    chat,
    container,
    date,
    state,
  }) {
    this.root = root;
    this.chat = chat;
    this.container = container;
    this.date = date;
    this.state = state;

    this.form = root.querySelector("#message-form");
    this.textarea = this.form?.querySelector("textarea");
    this.button = this.form?.querySelector("button");
    this.errors = document.getElementById("errors");

    this.isToday = false;
    this.draftKey = null;
    this.listeners = null;
    this.stopListeners = null;
  }

  init() {
    if (!this.form || !this.textarea || !this.button) {
      return;
    }

    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    this.isToday = this.date === today;
    this.draftKey = `chat-draft-${this.date}`;

    this.restoreDraft();
    this.configureForm();
    this.bindEvents();
  }

  restoreDraft() {
    if (!this.textarea) return;
    this.textarea.value = sessionStorage.getItem(this.draftKey) || "";
    this.resizeTextarea();
  }

  configureForm() {
    if (!this.textarea || !this.button) return;

    if (!this.isToday) {
      this.textarea.disabled = true;
      this.button.disabled = true;
      this.textarea.placeholder = "This day has past.";
    }
  }

  bindEvents() {
    if (!this.form || !this.textarea || !this.button) return;

    const container = this.container;
    this.listeners?.abort();
    const bag = createListenerBag();
    this.listeners = bag;

    const onAfterRequest = () => {
      sessionStorage.removeItem(this.draftKey);
      this.textarea.style.height = "auto";
      if (container) {
        container.scrollTop = container.scrollHeight;
      }
    };
    bag.add(this.form, "htmx:afterRequest", onAfterRequest);

    const userTimeInput = this.form.querySelector("#user-time");
    const onConfigRequest = (event) => {
      if (userTimeInput) {
        userTimeInput.value = new Date().toISOString();
      }
      if (!this.textarea.value.trim()) {
        event.preventDefault();
        this.textarea.focus({ preventScroll: true });
      }
    };
    bag.add(this.form, "htmx:configRequest", onConfigRequest);

    const onInput = () => {
      this.resizeTextarea();
      sessionStorage.setItem(this.draftKey, this.textarea.value);
      if (!this.state.currentStreamMsgId) {
        this.button.disabled = !this.textarea.value.trim();
      }
    };
    bag.add(this.textarea, "input", onInput);

    const onKeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (this.textarea.value.trim()) {
          this.form.requestSubmit();
          this.textarea.style.height = "auto";
        }
      }
    };
    bag.add(this.textarea, "keydown", onKeydown);

    if (this.errors) {
      const onErrorsAfterSwap = () => {
        requestAnimationFrame(() => {
          if (document.querySelector("#errors .error-box")) {
            this.setStreaming(false);
          }
        });
      };
      bag.add(this.errors, "htmx:afterSwap", onErrorsAfterSwap);
    }
  }

  resizeTextarea() {
    if (!this.textarea) return;
    this.textarea.style.height = "auto";
    this.textarea.style.height = this.textarea.scrollHeight + "px";
    if (this.container) {
      this.container.scrollTop = this.container.scrollHeight;
    }
  }

  setStreaming(streaming) {
    if (!this.button || !this.textarea) return;

    if (!this.isToday) {
      this.textarea.disabled = true;
      this.button.disabled = true;
      return;
    }

    if (streaming) {
      this.button.classList.add("stopping");
      this.button.type = "button";
      this.button.disabled = false;
      this.button.setAttribute("aria-label", "Stop");
      this.textarea.disabled = true;
      this.attachStopHandler();
    } else {
      this.stopListeners?.abort();
      this.stopListeners = null;
      this.button.classList.remove("stopping");
      this.button.type = "submit";
      this.textarea.disabled = false;
      this.button.disabled = !this.textarea.value.trim();
      this.textarea.focus({ preventScroll: true });
      this.button.setAttribute("aria-label", "Send");
    }
  }

  attachStopHandler() {
    if (!this.button) return;
    this.stopListeners?.abort();
    this.stopListeners = createListenerBag();
    this.stopListeners.add(this.button, "click", () => this.handleStopClick(), {
      once: true,
    });
  }

  handleStopClick() {
    if (!this.chat) return;
    const indicator = this.chat.querySelector("#typing-indicator");
    const stopEndpoint = indicator?.dataset.stopUrl;
    const wrap = indicator?.closest(".assistant-stream");
    if (wrap) {
      wrap.dispatchEvent(new Event("htmx:abort"));
      wrap.removeAttribute("hx-ext");
      wrap.removeAttribute("sse-connect");
      wrap.removeAttribute("sse-close");
      if (indicator) {
        indicator.classList.add("stopped");
        setTimeout(() => indicator.remove(), 1000);
      }
    }
    if (stopEndpoint) {
      htmx.ajax("POST", stopEndpoint, { swap: "none" });
    }
    this.state.currentStreamMsgId = null;
    this.setStreaming(false);
  }

  destroy() {
    this.listeners?.abort();
    this.listeners = null;
    this.stopListeners?.abort();
    this.stopListeners = null;
  }
}
