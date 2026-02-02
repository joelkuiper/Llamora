import { isNearBottom } from "../entries/scroll-utils.js";
import {
  findStreamByUserMsgId,
  findTypingIndicator,
} from "../entries/stream-utils.js";
import { getAlertContainer } from "../utils/alert-center.js";
import { ReactiveElement } from "../utils/reactive-element.js";

class EntryFormElement extends ReactiveElement {
  #entries = null;
  #container = null;
  #date = null;
  #form = null;
  #textarea = null;
  #session = null;
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
  #isSubmitting = false;
  #isStreaming = false;
  #streamingMsgId = null;
  #streamController = null;
  #controllerDisconnect = null;

  connectedCallback() {
    super.connectedCallback();
    this.#connected = true;
    this.#form = this.querySelector("form");
    this.#textarea = this.#form?.querySelector("textarea");
    this.#button = this.#form?.querySelector("button");
    this.#errors = getAlertContainer();
    if (!this.#date && this.dataset.date) {
      this.#date = this.dataset.date;
    }
    if (!this.#entries) {
      const entries = this.closest("#entries");
      if (entries) {
        this.#entries = entries;
      }
    }
    this.#maybeInit();
    this.#ensureControllerRegistration();
  }

  disconnectedCallback() {
    this.#connected = false;
    if (this.#controllerDisconnect) {
      this.#controllerDisconnect();
      this.#controllerDisconnect = null;
    }
    this.#teardown();
    this.#form = null;
    this.#textarea = null;
    this.#button = null;
    super.disconnectedCallback();
  }

  set entries(value) {
    this.#entries = value;
    this.#maybeInit();
  }

  set container(value) {
    this.#container = value || null;
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

  set streamingMsgId(value) {
    const normalized = value ? String(value) : null;
    this.#streamingMsgId = normalized;
    if (normalized) {
      this.dataset.streamingMsgId = normalized;
    } else {
      delete this.dataset.streamingMsgId;
    }
  }


  set session(value) {
    if (this.#session === value) {
      return;
    }

    this.#session = value || null;

    if (this.#initialized) {
      const currentId = this.#session?.currentMsgId || null;
      this.streamingMsgId = currentId;
      this.setStreaming(Boolean(currentId));
    }
  }

  set streamController(value) {
    if (this.#streamController === value) {
      return;
    }
    if (this.#controllerDisconnect) {
      this.#controllerDisconnect();
      this.#controllerDisconnect = null;
    }
    this.#streamController = value || null;
    this.#ensureControllerRegistration();
  }


  get streamingMsgId() {
    return this.#streamingMsgId;
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
    if (!this.#entries || !this.#date) {
      return;
    }

    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(
      now.getDate()
    )}`;
    this.#isToday = this.#date === today;
    this.#draftKey = `entry-draft-${this.#date}`;

    this.streamingMsgId = this.#session?.currentMsgId || null;

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

  #ensureControllerRegistration() {
    if (!this.#streamController || !this.#connected) {
      return;
    }

    if (this.#controllerDisconnect) {
      return;
    }

    if (typeof this.#streamController.registerForm === "function") {
      const cleanup = this.#streamController.registerForm(this);
      if (typeof cleanup === "function") {
        this.#controllerDisconnect = cleanup;
      }
    }
  }

  #teardown() {
    this.#setSubmitting(false);
    this.#listeners = this.disposeListenerBag(this.#listeners);
    this.#stopListeners = this.disposeListenerBag(this.#stopListeners);
    this.#streamFocusListeners = this.disposeListenerBag(
      this.#streamFocusListeners
    );
    this.#shouldRestoreFocus = false;
    this.#initialized = false;
    this.#isStreaming = false;
    this.#isSubmitting = false;
    this.streamingMsgId = null;
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

    this.watchHtmxRequests(this.#form, {
      bag,
      within: this.#form,
      onStart: () => {
        if (!this.#isToday || this.#isStreaming) {
          return;
        }
        this.#setSubmitting(true);
      },
      onEnd: (event) => {
        if (
          event?.type === "htmx:responseError" ||
          event?.type === "htmx:sendError"
        ) {
          this.#setSubmitting(false);
          this.setStreaming(false);
          return;
        }
        this.#setSubmitting(false);
      },
    });

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
        ? isNearBottom(this.#container, 16)
        : false;
      this.#resizeTextarea({ forceScroll: shouldForceScroll });
      if (this.#draftKey) {
        sessionStorage.setItem(this.#draftKey, this.#textarea.value);
      }
      if (!this.#session?.currentMsgId && !this.#isSubmitting) {
        this.#button.disabled = !this.#textarea.value.trim();
      }
    };
    bag.add(this.#textarea, "input", onInput);

    const onKeydown = (e) => {
      if (e.isComposing || e.keyCode === 229) {
        return;
      }
      if (
        e.key === "Enter" &&
        !e.shiftKey &&
        !this.#isSubmitting &&
        !this.#isStreaming
      ) {
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
      ? isNearBottom(this.#container, 16)
      : false;
    this.#textarea.style.height = "auto";
    this.#textarea.style.height = this.#textarea.scrollHeight + "px";
    if (this.#container && (forceScroll || wasNearBottom)) {
      this.#container.scrollTop = this.#container.scrollHeight;
    }
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

    this.#isStreaming = !!streaming;
    this.#setSubmitting(false);

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
    if (!this.#entries) return;
    const indicator = this.#getTypingIndicator();
    const currentId =
      this.#streamingMsgId ||
      this.#session?.currentMsgId ||
      indicator?.dataset.userMsgId ||
      null;
    let stream = currentId
      ? findStreamByUserMsgId(this.#entries, currentId)
      : null;
    if (!stream && indicator) {
      stream = indicator.closest("llm-stream");
    }
    const stopEndpoint = stream?.dataset?.stopUrl || indicator?.dataset?.stopUrl;
    const abortedViaController = this.#streamController?.abortActiveStream({
      reason: "entry-form:stop",
    });

    let abortedLocally = false;
    if (!abortedViaController) {
      if (stream && typeof stream.abort === "function") {
        stream.abort();
        abortedLocally = true;
      } else if (indicator) {
        indicator.classList.add("stopped");
        setTimeout(() => indicator.remove(), 1000);
      }
    }

    if (stopEndpoint) {
      htmx.ajax("POST", stopEndpoint, { swap: "none" });
    }

    if (!abortedViaController && !abortedLocally && this.#session) {
      this.#session.abort({ reason: "entry-form:stop" });
    }
  }

  #getTypingIndicator() {
    if (!this.#entries) return null;
    const targetId = this.#streamingMsgId || this.#session?.currentMsgId || null;
    return findTypingIndicator(this.#entries, targetId);
  }

  #setSubmitting(value) {
    if (!this.#form || !this.#button || !this.#textarea) return;
    if (value && !this.#isToday) {
      return;
    }

    if (this.#isSubmitting === value) {
      return;
    }

    this.#isSubmitting = value;

    if (value) {
      this.#form.classList.add("is-submitting");
      this.#form.setAttribute("aria-busy", "true");
      this.#button.classList.add("submitting");
      this.#button.setAttribute("aria-busy", "true");
      this.#textarea.disabled = true;
      this.#button.disabled = true;
    } else {
      this.#form.classList.remove("is-submitting");
      this.#form.removeAttribute("aria-busy");
      this.#button.classList.remove("submitting");
      this.#button.removeAttribute("aria-busy");
      if (!this.#isToday || this.#isStreaming) {
        return;
      }
      this.#textarea.disabled = false;
      this.#button.disabled = !this.#textarea.value.trim();
    }
  }

  handleStreamStatus(detail) {
    const info = detail || {};
    const type = info.type || "statuschange";
    const currentId = info.currentMsgId ?? null;

    if (type === "begin") {
      const activeId = currentId || info.userMsgId || null;
      this.streamingMsgId = activeId;
      this.setStreaming(true);
      return;
    }

    if (type === "abort" || type === "complete") {
      const targetId = info.userMsgId || null;
      if (!targetId || targetId === this.streamingMsgId) {
        this.streamingMsgId = currentId || null;
        this.setStreaming(false);
      }
      return;
    }

    if (info.streaming) {
      const activeId = currentId || info.userMsgId || null;
      this.streamingMsgId = activeId;
      this.setStreaming(true);
      return;
    }

    const targetId =
      info.userMsgId ?? info.previousMsgId ?? this.streamingMsgId;
    if (!targetId || targetId === this.streamingMsgId) {
      this.streamingMsgId = currentId || null;
      this.setStreaming(false);
    }
  }
}

if (!customElements.get("entry-form")) {
  customElements.define("entry-form", EntryFormElement);
}
