import { ReactiveElement } from "../utils/reactive-element.js";

class ChatFormElement extends ReactiveElement {
  #chat = null;
  #container = null;
  #state = null;
  #date = null;
  #form = null;
  #textarea = null;
  #button = null;
  #errors = null;
  #isToday = false;
  #draftKey = null;
  #listeners = null;
  #stopListeners = null;
  #streamFocusListeners = null;
  #connected = false;
  #initialized = false;
  #shouldRestoreFocus = false;
  #pendingStreamingState = null;

  connectedCallback() {
    super.connectedCallback();
    this.#connected = true;
    this.#form = this.querySelector("form");
    this.#textarea = this.#form?.querySelector("textarea");
    this.#button = this.#form?.querySelector("button");
    this.#errors = document.getElementById("errors");
    if (!this.#date && this.dataset.date) {
      this.#date = this.dataset.date;
    }
    if (!this.#chat) {
      const chat = this.closest("#chat");
      if (chat) {
        this.#chat = chat;
      }
    }
    this.#maybeInit();
  }

  disconnectedCallback() {
    this.#connected = false;
    this.#teardown();
    this.#form = null;
    this.#textarea = null;
    this.#button = null;
    super.disconnectedCallback();
  }

  set chat(value) {
    this.#chat = value;
    this.#maybeInit();
  }

  set container(value) {
    this.#container = value || null;
  }

  set state(value) {
    this.#state = value || null;
    this.#maybeInit();
  }

  set date(value) {
    this.#date = value || null;
    if (value) {
      this.dataset.date = value;
    } else {
      delete this.dataset.date;
    }
    this.#maybeInit();
  }

  get isToday() {
    return this.#isToday;
  }

  #maybeInit() {
    if (!this.#connected) {
      return;
    }
    if (this.#initialized) {
      return;
    }
    if (!this.#form || !this.#textarea || !this.#button) {
      return;
    }
    if (!this.#state || !this.#chat || !this.#date) {
      return;
    }

    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(
      now.getDate()
    )}`;
    this.#isToday = this.#date === today;
    this.#draftKey = `chat-draft-${this.#date}`;

    this.#restoreDraft();
    this.#configureForm();
    this.#bindEvents();

    this.#initialized = true;

    if (this.#pendingStreamingState !== null) {
      const pending = this.#pendingStreamingState;
      this.#pendingStreamingState = null;
      this.setStreaming(pending);
    }
  }

  #teardown() {
    this.#listeners = this.disposeListenerBag(this.#listeners);
    this.#stopListeners = this.disposeListenerBag(this.#stopListeners);
    this.#streamFocusListeners = this.disposeListenerBag(
      this.#streamFocusListeners
    );
    this.#shouldRestoreFocus = false;
    this.#initialized = false;
  }

  #restoreDraft() {
    if (!this.#textarea || !this.#draftKey) return;
    this.#textarea.value = sessionStorage.getItem(this.#draftKey) || "";
    this.#resizeTextarea();
  }

  #configureForm() {
    if (!this.#textarea || !this.#button) return;

    if (!this.#isToday) {
      this.#textarea.disabled = true;
      this.#button.disabled = true;
      this.#textarea.placeholder = "This day has passed.";
    }
  }

  #bindEvents() {
    if (!this.#form || !this.#textarea || !this.#button) return;

    this.#listeners = this.resetListenerBag(this.#listeners);
    const bag = this.#listeners;

    const onAfterRequest = () => {
      if (!this.#draftKey) return;
      requestAnimationFrame(() => {
        if (!this.#draftKey) return;
        sessionStorage.removeItem(this.#draftKey);
        this.#resizeTextarea({ forceScroll: true });
      });
    };
    bag.add(this.#form, "htmx:afterRequest", onAfterRequest);

    const userTimeInput = this.#form.querySelector("#user-time");
    const onConfigRequest = (event) => {
      if (userTimeInput) {
        userTimeInput.value = new Date().toISOString();
      }
      if (!this.#textarea.value.trim()) {
        event.preventDefault();
        this.#textarea.focus({ preventScroll: true });
      }
    };
    bag.add(this.#form, "htmx:configRequest", onConfigRequest);

    const onInput = () => {
      const shouldForceScroll = this.#container
        ? this.#isNearBottom(this.#container)
        : false;
      this.#resizeTextarea({ forceScroll: shouldForceScroll });
      if (this.#draftKey) {
        sessionStorage.setItem(this.#draftKey, this.#textarea.value);
      }
      if (!this.#state?.currentStreamMsgId) {
        this.#button.disabled = !this.#textarea.value.trim();
      }
    };
    bag.add(this.#textarea, "input", onInput);

    const onKeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (this.#textarea.value.trim()) {
          this.#form.requestSubmit();
          this.#textarea.style.height = "auto";
        }
      }
    };
    bag.add(this.#textarea, "keydown", onKeydown);

    if (this.#errors) {
      const onErrorsAfterSwap = () => {
        requestAnimationFrame(() => {
          if (document.querySelector("#errors .alert")) {
            this.setStreaming(false);
          }
        });
      };
      bag.add(this.#errors, "htmx:afterSwap", onErrorsAfterSwap);
    }
  }

  #resizeTextarea({ forceScroll = false } = {}) {
    if (!this.#textarea) return;
    const wasNearBottom = this.#container
      ? this.#isNearBottom(this.#container)
      : false;
    this.#textarea.style.height = "auto";
    this.#textarea.style.height = this.#textarea.scrollHeight + "px";
    if (this.#container && (forceScroll || wasNearBottom)) {
      this.#container.scrollTop = this.#container.scrollHeight;
    }
  }

  #isNearBottom(element) {
    const threshold = 16;
    const distance =
      element.scrollHeight - (element.scrollTop + element.clientHeight);
    return distance <= threshold;
  }

  setStreaming(streaming) {
    if (!this.#initialized) {
      this.#pendingStreamingState = streaming;
      return;
    }

    if (!this.#button || !this.#textarea) return;

    if (!this.#isToday) {
      this.#textarea.disabled = true;
      this.#button.disabled = true;
      return;
    }

    if (streaming) {
      this.#streamFocusListeners = this.disposeListenerBag(
        this.#streamFocusListeners
      );
      const active = document.activeElement;
      this.#shouldRestoreFocus = !!(
        !active ||
        active === document.body ||
        this.#form?.contains(active)
      );
      if (this.#shouldRestoreFocus) {
        const bag = this.resetListenerBag(this.#streamFocusListeners);
        this.#streamFocusListeners = bag;
        const cancelRestore = () => {
          this.#shouldRestoreFocus = false;
          this.#streamFocusListeners = this.disposeListenerBag(
            this.#streamFocusListeners
          );
        };
        bag.add(document, "pointerdown", (event) => {
          if (!this.#form?.contains(event.target)) {
            cancelRestore();
          }
        });
        bag.add(document, "focusin", (event) => {
          if (
            event.target &&
            event.target !== document.body &&
            !this.#form?.contains(event.target)
          ) {
            cancelRestore();
          }
        });
      }
      this.#button.classList.add("stopping");
      this.#button.type = "button";
      this.#button.disabled = false;
      this.#button.setAttribute("aria-label", "Stop");
      this.#textarea.disabled = true;
      this.#attachStopHandler();
    } else {
      this.#stopListeners = this.disposeListenerBag(this.#stopListeners);
      this.#streamFocusListeners = this.disposeListenerBag(
        this.#streamFocusListeners
      );
      this.#button.classList.remove("stopping");
      this.#button.type = "submit";
      this.#textarea.disabled = false;
      this.#button.disabled = !this.#textarea.value.trim();
      const active = document.activeElement;
      if (
        this.#shouldRestoreFocus &&
        (!active ||
          active === document.body ||
          this.#form?.contains(active))
      ) {
        this.#textarea.focus({ preventScroll: true });
      }
      this.#shouldRestoreFocus = false;
      this.#button.setAttribute("aria-label", "Send");
    }
  }

  #attachStopHandler() {
    if (!this.#button) return;
    this.#stopListeners = this.resetListenerBag(this.#stopListeners);
    this.#stopListeners.add(this.#button, "click", () => this.#handleStopClick(), {
      once: true,
    });
  }

  #handleStopClick() {
    if (!this.#chat) return;
    const indicator = this.#chat.querySelector("#typing-indicator");
    const stopEndpoint = indicator?.dataset.stopUrl;
    const stream = indicator?.closest("llm-stream");
    if (stream && typeof stream.abort === "function") {
      stream.abort();
    } else if (indicator) {
      indicator.classList.add("stopped");
      setTimeout(() => indicator.remove(), 1000);
    }
    if (stopEndpoint) {
      htmx.ajax("POST", stopEndpoint, { swap: "none" });
    }
    if (this.#state) {
      this.#state.currentStreamMsgId = null;
    }
    this.setStreaming(false);
  }
}

if (!customElements.get("chat-form")) {
  customElements.define("chat-form", ChatFormElement);
}
