import { ScrollController } from "../chat/scroll-controller.js";
import { MarkdownObserver } from "../chat/markdown-observer.js";
import { renderMarkdownInElement } from "../markdown.js";
import { initDayNav } from "../day.js";
import { scrollToHighlight } from "../ui.js";
import { setTimezoneCookie } from "../timezone.js";
import { createListenerBag } from "../utils/events.js";
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

export class ChatView extends HTMLElement {
  #chatForm = null;
  #scrollController = null;
  #state = null;
  #chat = null;
  #scrollToBottom = null;
  #midnightCleanup = null;
  #afterSwapHandler;
  #beforeSwapHandler;
  #pageShowHandler;
  #connectionListeners = null;
  #chatListeners = null;
  #markdownObserver = null;
  #initialized = false;

  constructor() {
    super();
    this.#afterSwapHandler = (event) => this.#handleChatAfterSwap(event);
    this.#beforeSwapHandler = (event) => this.#handleChatBeforeSwap(event);
    this.#pageShowHandler = (event) => this.#handlePageShow(event);
  }

  connectedCallback() {
    if (!this.style.display) {
      this.style.display = "block";
    }

    this.#connectionListeners?.abort();
    this.#connectionListeners = createListenerBag();
    this.#connectionListeners.add(window, "pageshow", this.#pageShowHandler);

    if (!this.#initialized) {
      this.#initialize();
    }
  }

  disconnectedCallback() {
    this.#teardown();
    this.#connectionListeners?.abort();
    this.#connectionListeners = null;
    this.#initialized = false;
  }

  #initialize() {
    this.#teardown();

    setTimezoneCookie();

    const chat = this.querySelector("#chat");
    if (!chat) {
      this.#chat = null;
      return;
    }

    const container = document.getElementById("content-wrapper");

    this.#state = { currentStreamMsgId: null };
    this.#chat = chat;

    chat.querySelectorAll?.(".markdown-body").forEach((el) => {
      if (el?.dataset?.rendered) {
        delete el.dataset.rendered;
      }

      if (!el?.querySelector?.("#typing-indicator")) {
        renderMarkdownInElement(el);
      }
    });

    this.#chatForm = this.querySelector("chat-form");
    if (this.#chatForm) {
      this.#chatForm.chat = chat;
      this.#chatForm.container = container;
      this.#chatForm.state = this.#state;
      this.#chatForm.date = chat.dataset.date;
    }

    if (this.#chatForm?.isToday) {
      this.#midnightCleanup = scheduleMidnightRefresh(chat);
    }

    this.#scrollController = new ScrollController({ root: document, chat });
    this.#scrollToBottom = this.#scrollController.init() || (() => {});

    this.#configureStreams(chat);

    this.#chatListeners?.abort();
    this.#chatListeners = createListenerBag();
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
    this.#updateStreamingState();

    initDayNav();
    scrollToHighlight(this.dataset.scrollTarget);

    if (chat.dataset.date) {
      document.title = chat.dataset.date;
    }

    this.#initialized = true;
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

    this.#chatListeners?.abort();
    this.#chatListeners = null;

    this.#chat = null;
    this.#scrollToBottom = null;
    this.#chatForm = null;
    this.#state = null;
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
      this.#initialize();
    }
  }

  #updateStreamingState(forceScroll = false) {
    if (!this.#chat || !this.#chatForm) return;

    const msgId = findCurrentMsgId(this.#chat);
    this.#state.currentStreamMsgId = msgId;
    this.#chatForm.setStreaming(Boolean(msgId));

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
    this.#chatForm?.setStreaming(true);
    this.#scrollToBottom?.(true);
  }

  #handleStreamComplete(event) {
    const detail = event?.detail || {};
    if (this.#state) {
      this.#state.currentStreamMsgId = null;
    }
    this.#chatForm?.setStreaming(false);
    if (detail.status !== "aborted") {
      this.#scrollToBottom?.(true);
    }
  }
}
