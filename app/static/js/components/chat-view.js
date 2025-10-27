import { ChatFormController } from "../chat/form-controller.js";
import { ScrollController } from "../chat/scroll-controller.js";
import { StreamController } from "../chat/stream-controller.js";
import { renderAllMarkdown } from "../markdown.js";
import { initTagPopovers } from "../meta-chips.js";
import { initDayNav } from "../day.js";
import { initSearchUI, scrollToHighlight } from "../ui.js";
import { setTimezoneCookie } from "../timezone.js";
import { createListenerBag } from "../utils/events.js";

const TYPING_INDICATOR_SELECTOR = "#typing-indicator";

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
  #formController = null;
  #scrollController = null;
  #streamController = null;
  #observer = null;
  #state = null;
  #chat = null;
  #scrollToBottom = null;
  #midnightCleanup = null;
  #afterSwapHandler;
  #pageShowHandler;
  #connectionListeners = null;
  #chatListeners = null;
  #initialized = false;

  constructor() {
    super();
    this.#afterSwapHandler = (event) => this.#handleChatAfterSwap(event);
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

    chat.querySelectorAll(".meta-chips").forEach((chips) => {
      delete chips.dataset.popInit;
    });

    this.#state = { currentStreamMsgId: null };
    this.#chat = chat;

    this.#formController = new ChatFormController({
      root: document,
      chat,
      container,
      date: chat.dataset.date,
      state: this.#state,
    });
    this.#formController.init();

    if (this.#formController.isToday) {
      this.#midnightCleanup = scheduleMidnightRefresh(chat);
    }

    this.#scrollController = new ScrollController({ root: document, chat });
    this.#scrollToBottom = this.#scrollController.init() || (() => {});

    this.#streamController = new StreamController({
      chat,
      state: this.#state,
      setStreaming: (streaming) => this.#formController?.setStreaming(streaming),
      scrollToBottom: (...args) => this.#scrollToBottom?.(...args),
    });
    this.#streamController.init();

    this.#chatListeners?.abort();
    this.#chatListeners = createListenerBag();
    this.#chatListeners.add(chat, "htmx:afterSwap", this.#afterSwapHandler);

    this.#observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            node.classList?.remove("no-anim");
            node.querySelectorAll?.(".no-anim").forEach((el) =>
              el.classList.remove("no-anim")
            );
          }
        });
      }
    });
    this.#observer.observe(chat, { childList: true });

    renderAllMarkdown(chat);
    initTagPopovers(chat);
    this.#updateStreamingState();

    initDayNav();
    initSearchUI();
    scrollToHighlight();

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

    this.#streamController?.destroy();
    this.#streamController = null;

    this.#scrollController?.destroy();
    this.#scrollController = null;

    this.#formController?.destroy();
    this.#formController = null;


    this.#observer?.disconnect();
    this.#observer = null;

    this.#chatListeners?.abort();
    this.#chatListeners = null;

    this.#chat = null;
    this.#scrollToBottom = null;
    this.#state = null;
  }

  #handleChatAfterSwap(event) {
    if (!this.#chat) return;

    renderAllMarkdown(this.#chat);
    initTagPopovers(this.#chat);

    if (event.target === this.#chat) {
      this.#updateStreamingState(true);
    }
  }

  #handlePageShow(event) {
    if (event.persisted) {
      this.#initialize();
    }
  }

  #updateStreamingState(forceScroll = false) {
    if (!this.#chat || !this.#formController) return;

    const msgId = findCurrentMsgId(this.#chat);
    this.#state.currentStreamMsgId = msgId;
    this.#formController.setStreaming(Boolean(msgId));

    if (forceScroll) {
      this.#scrollToBottom?.(true);
    }
  }
}
