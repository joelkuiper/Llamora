import { ScrollController } from "../chat/scroll-controller.js";
import { MarkdownObserver } from "../chat/markdown-observer.js";
import { renderMarkdownInElement } from "../markdown.js";
import { initDayNav } from "../day.js";
import { scrollToHighlight } from "../ui.js";
import { setTimezoneCookie } from "../timezone.js";
import { createListenerBag } from "../utils/events.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import "./chat-form.js";
import "./llm-stream.js";

const TYPING_INDICATOR_SELECTOR = "#typing-indicator";

function activateAnimations(node) {
  if (!node || node.nodeType !== Node.ELEMENT_NODE) return;

  node.classList?.remove("no-anim");
  node.querySelectorAll?.(".no-anim").forEach((el) => {
    el.classList.remove("no-anim");
  });
}

function findCurrentMsgId(chat) {
  if (!chat) return null;
  const indicator = chat.querySelector(TYPING_INDICATOR_SELECTOR);
  if (!indicator) return null;
  return indicator.dataset.userMsgId || "opening";
}

function scheduleMidnightRefresh(chat) {
  if (!chat) return () => {};

  let timeoutId = null;
  const listeners = createListenerBag();

  const pad = (value) => String(value).padStart(2, "0");

  const runCheck = () => {
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }

    const now = new Date();
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(
      now.getDate()
    )}`;

    if (chat.dataset.date !== today) {
      setTimezoneCookie();
      window.location.href = "/d/today";
      return;
    }

    const nextMidnight = new Date(now);
    nextMidnight.setHours(24, 0, 0, 0);
    timeoutId = window.setTimeout(runCheck, nextMidnight.getTime() - now.getTime());
  };

  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      runCheck();
    }
  };

  listeners.add(document, "visibilitychange", handleVisibility);
  runCheck();

  return () => {
    listeners.abort();
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  };
}

export class ChatView extends ReactiveElement {
  #chatForm = null;
  #scrollController = null;
  #state = null;
  #chat = null;
  #scrollToBottom = null;
  #midnightCleanup = null;
  #afterSwapHandler;
  #beforeSwapHandler;
  #pageShowHandler;
  #historyRestoreHandler;
  #historyRestoreFrame = null;
  #connectionListeners = null;
  #chatListeners = null;
  #markdownObserver = null;
  #initialized = false;
  #lastRenderedDay = null;
  #chatFormReady = Promise.resolve();

  constructor() {
    super();
    this.#afterSwapHandler = (event) => this.#handleChatAfterSwap(event);
    this.#beforeSwapHandler = (event) => this.#handleChatBeforeSwap(event);
    this.#pageShowHandler = (event) => this.#handlePageShow(event);
    this.#historyRestoreHandler = (event) => this.#handleHistoryRestore(event);
  }

  connectedCallback() {
    super.connectedCallback();
    if (!this.style.display) {
      this.style.display = "block";
    }

    this.#connectionListeners = this.resetListenerBag(this.#connectionListeners);
    this.#connectionListeners.add(window, "pageshow", this.#pageShowHandler);
    this.#connectionListeners.add(
      document.body,
      "htmx:historyRestore",
      this.#historyRestoreHandler
    );

    this.#syncToChatDate();
  }

  disconnectedCallback() {
    this.#cancelHistoryRestoreFrame();
    this.#teardown();
    this.#connectionListeners = this.disposeListenerBag(this.#connectionListeners);
    this.#initialized = false;
    super.disconnectedCallback();
  }

  #initialize(
    chat = this.querySelector("#chat"),
    chatDate = chat?.dataset?.date ?? null
  ) {
    this.#initialized = false;
    this.#teardown();

    setTimezoneCookie();

    if (!chat) {
      this.#chat = null;
      this.#lastRenderedDay = null;
      if (document?.body?.dataset) {
        delete document.body.dataset.activeDay;
        delete document.body.dataset.activeDayLabel;
      }
      return;
    }

    const container = document.getElementById("content-wrapper");

    this.#state = { currentStreamMsgId: null };
    this.#chat = chat;

    const activeDay = chatDate || null;
    const activeDayLabel = chat?.dataset?.longDate ?? null;
    this.#lastRenderedDay = activeDay;

    if (document?.body?.dataset) {
      if (activeDay) {
        document.body.dataset.activeDay = activeDay;
      } else {
        delete document.body.dataset.activeDay;
      }
      if (activeDayLabel) {
        document.body.dataset.activeDayLabel = activeDayLabel;
      } else {
        delete document.body.dataset.activeDayLabel;
      }
    }

    chat.querySelectorAll?.(".markdown-body").forEach((el) => {
      if (el?.dataset?.rendered) {
        delete el.dataset.rendered;
      }

      if (!el?.querySelector?.("#typing-indicator")) {
        renderMarkdownInElement(el);
      }
    });

    this.#chatForm = this.querySelector("chat-form");
    let chatFormReady = Promise.resolve();
    if (this.#chatForm) {
      const currentForm = this.#chatForm;
      chatFormReady = this.#wireChatForm(currentForm, {
        chat,
        container,
        state: this.#state,
        date: activeDay,
      });
      this.#chatFormReady = chatFormReady.then(() => {
        if (this.#chatForm !== currentForm) return;
        const currentMsgId = this.#state?.currentStreamMsgId ?? null;
        if (typeof this.#chatForm.setStreaming === "function") {
          this.#chatForm.setStreaming(Boolean(currentMsgId));
        }
      });
    } else {
      this.#chatFormReady = Promise.resolve();
    }

    if (this.#chatForm?.isToday) {
      this.#midnightCleanup = scheduleMidnightRefresh(chat);
    }

    this.#scrollController = new ScrollController({ root: document, chat });
    this.#scrollToBottom = this.#scrollController.init() || (() => {});

    this.#configureStreams(chat);

    this.#chatListeners = this.resetListenerBag(this.#chatListeners);
    this.#chatListeners.add(chat, "htmx:afterSwap", this.#afterSwapHandler);
    this.#chatListeners.add(chat, "htmx:beforeSwap", this.#beforeSwapHandler);
    this.#chatListeners.add(chat, "llm-stream:start", (event) =>
      this.#handleStreamStart(event)
    );
    this.#chatListeners.add(chat, "llm-stream:complete", (event) =>
      this.#handleStreamComplete(event)
    );

    activateAnimations(chat);

    this.#markdownObserver = new MarkdownObserver({
      root: chat,
      onRender: (el) => this.#handleMarkdownRendered(el),
    });
    this.#markdownObserver.start();
    chatFormReady.then(() => this.#updateStreamingState());

    initDayNav(chat, { activeDay, label: activeDayLabel });
    scrollToHighlight(this.dataset.scrollTarget);

    if (activeDayLabel) {
      document.title = activeDayLabel;
    } else if (activeDay) {
      document.title = activeDay;
    }

    this.#initialized = true;
  }

  #syncToChatDate() {
    const chat = this.querySelector("#chat");
    const chatDate = chat?.dataset?.date ?? null;
    const chatChanged = chat !== this.#chat;
    const dateChanged = chatDate !== this.#lastRenderedDay;

    if (!chat) {
      if (this.#initialized || this.#chat || this.#lastRenderedDay) {
        this.#initialize(null, null);
      }
      return;
    }

    if (!this.#initialized || chatChanged || dateChanged) {
      this.#initialize(chat, chatDate);
    }
  }

  #teardown() {
    if (this.#midnightCleanup) {
      this.#midnightCleanup();
      this.#midnightCleanup = null;
    }

    this.#scrollController?.destroy();
    this.#scrollController = null;

    this.#markdownObserver?.stop();
    this.#markdownObserver = null;

    this.#chatListeners = this.disposeListenerBag(this.#chatListeners);

    this.#chat = null;
    this.#scrollToBottom = null;
    this.#chatForm = null;
    this.#state = null;
    this.#chatFormReady = Promise.resolve();
  }

  #handleChatBeforeSwap(event) {
    if (!this.#chat || !this.#markdownObserver) return;

    const swapTargets = this.#collectSwapTargets(event);
    if (swapTargets.includes(this.#chat)) {
      this.#markdownObserver.pause();
    }
  }

  #handleChatAfterSwap(event) {
    if (!this.#chat) return;

    const swapTargets = this.#collectSwapTargets(event);

    swapTargets.forEach((target) => {
      if (target === this.#chat) {
        activateAnimations(target);
        return;
      }

      if (target?.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
        target.querySelectorAll?.(".message").forEach((node) => {
          activateAnimations(node);
        });
        return;
      }

      if (target?.classList?.contains("message")) {
        activateAnimations(target);
      }
    });

    this.#markdownObserver?.resume(swapTargets);

    swapTargets.forEach((target) => {
      this.#configureStreams(target);
    });

    if (swapTargets.includes(this.#chat)) {
      this.#updateStreamingState(true);
    }

    this.#syncToChatDate();
  }

  #collectSwapTargets(event) {
    const nodes = new Set();

    const addNode = (node) => {
      if (!node || !(node instanceof Node)) return;
      nodes.add(node);
    };

    addNode(event.target);

    const detail = event.detail || {};

    addNode(detail.target);
    addNode(detail.swapTarget);

    if (detail.targets && typeof detail.targets[Symbol.iterator] === "function") {
      for (const node of detail.targets) {
        addNode(node);
      }
    }

    return Array.from(nodes);
  }

  #handlePageShow(event) {
    if (event.persisted) {
      this.#initialized = false;
      this.#syncToChatDate();
    }
  }

  #handleHistoryRestore() {
    this.#initialized = false;
    this.#cancelHistoryRestoreFrame();
    this.#historyRestoreFrame = window.requestAnimationFrame(() => {
      this.#historyRestoreFrame = null;
      this.#syncToChatDate();
      if (this.#chatForm) {
        const currentForm = this.#chatForm;
        const chatFormReady = this.#wireChatForm(currentForm, {
          chat: this.#chat,
          container: document.getElementById("content-wrapper"),
          state: this.#state,
          date: this.#lastRenderedDay,
        });
        this.#chatFormReady = chatFormReady.then(() => {
          if (this.#chatForm !== currentForm) return;
          const currentMsgId = this.#state?.currentStreamMsgId ?? null;
          if (typeof this.#chatForm.setStreaming === "function") {
            this.#chatForm.setStreaming(Boolean(currentMsgId));
          }
        });
        this.#chatFormReady.then(() => this.#updateStreamingState());
      } else {
        this.#chatFormReady = Promise.resolve();
      }
    });
  }

  async #wireChatForm(chatForm, { chat, container, state, date }) {
    if (!chatForm) return;
    await customElements.whenDefined("chat-form");
    if (!chatForm.isConnected) return;
    customElements.upgrade(chatForm);
    chatForm.container = container;
    chatForm.chat = chat;
    chatForm.state = state;
    chatForm.date = date;
  }

  #cancelHistoryRestoreFrame() {
    if (this.#historyRestoreFrame != null) {
      window.cancelAnimationFrame(this.#historyRestoreFrame);
      this.#historyRestoreFrame = null;
    }
  }

  async #updateStreamingState(forceScroll = false) {
    if (!this.#chat || !this.#chatForm) return;

    await this.#chatFormReady;

    if (!this.#chat || !this.#chatForm) return;

    const msgId = findCurrentMsgId(this.#chat);
    this.#state.currentStreamMsgId = msgId;
    if (typeof this.#chatForm.setStreaming === "function") {
      this.#chatForm.setStreaming(Boolean(msgId));
    }

    if (forceScroll) {
      this.#scrollToBottom?.(true);
    }
  }

  #handleMarkdownRendered(el) {
    const stream = el?.closest?.("llm-stream");
    stream?.handleMarkdownRendered(el);
  }

  #configureStreams(root) {
    if (!root) return;

    const apply = (stream) => {
      if (!stream) return;
      stream.scrollToBottom = (...args) => this.#scrollToBottom?.(...args);
    };

    if (root instanceof Element && root.matches("llm-stream")) {
      apply(root);
    }

    root.querySelectorAll?.("llm-stream").forEach((stream) => apply(stream));
  }

  #handleStreamStart(event) {
    const detail = event?.detail || {};
    if (this.#state) {
      this.#state.currentStreamMsgId = detail.userMsgId || null;
    }
    if (typeof this.#chatForm?.setStreaming === "function") {
      this.#chatForm.setStreaming(true);
    }
    this.#scrollToBottom?.(true);
  }

  #handleStreamComplete(event) {
    const detail = event?.detail || {};
    if (this.#state) {
      this.#state.currentStreamMsgId = null;
    }
    if (typeof this.#chatForm?.setStreaming === "function") {
      this.#chatForm.setStreaming(false);
    }
    if (detail.status !== "aborted") {
      this.#scrollToBottom?.(true);
    }
  }
}
