import { scrollEvents } from "../chat/scroll-manager.js";
import { MarkdownObserver } from "../chat/markdown-observer.js";
import { StreamingSession } from "../chat/streaming-session.js";
import { renderMarkdownInElement } from "../markdown.js";
import { initDayNav, navigateToDate } from "../day.js";
import { scrollToHighlight } from "../ui.js";
import { setTimezoneCookie } from "../timezone.js";
import { createListenerBag } from "../utils/events.js";
import { TYPING_INDICATOR_SELECTOR } from "../typing-indicator.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import { setActiveDay, clearActiveDay } from "../chat/active-day-store.js";
import "./chat-form.js";
import "./llm-stream.js";

function formatDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "";
  }

  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function activateAnimations(node) {
  if (!node || node.nodeType !== Node.ELEMENT_NODE) return;

  node.classList?.remove("no-anim");
  node.querySelectorAll?.(".no-anim").forEach((el) => {
    el.classList.remove("no-anim");
  });
}

function scheduleMidnightRefresh(chat) {
  if (!chat) return () => {};

  let timeoutId = null;
  const listeners = createListenerBag();

  const runCheck = () => {
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }

    const now = new Date();
    const updateClientToday = window?.appInit?.updateClientToday;
    const today =
      typeof updateClientToday === "function"
        ? updateClientToday()
        : formatDate(now);
    if (typeof updateClientToday !== "function" && document?.body?.dataset) {
      document.body.dataset.clientToday = today;
    }

    if (chat.dataset.date !== today) {
      const timezone = setTimezoneCookie();
      const zone =
        typeof timezone === "string" && timezone ? timezone : "UTC";
      try {
        const url = new URL("/d/today", window.location.origin);
        url.searchParams.set("tz", zone);
        window.location.href = `${url.pathname}${url.search}`;
      } catch (err) {
        const encoded = encodeURIComponent(zone);
        window.location.href = `/d/today?tz=${encoded}`;
      }
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
  #scrollManager = null;
  #scrollEventListeners = null;
  #chat = null;
  #midnightCleanup = null;
  #afterSwapHandler;
  #beforeSwapHandler;
  #pageShowHandler;
  #historyRestoreHandler;
  #historyRestoreFrame = null;
  #connectionListeners = null;
  #chatListeners = null;
  #session = null;
  #sessionListeners = null;
  #markdownObserver = null;
  #initialized = false;
  #lastRenderedDay = null;
  #chatFormReady = Promise.resolve();
  #pendingScrollTarget = null;
  #forceNavFlash = false;

  constructor() {
    super();
    this.#afterSwapHandler = (event) => this.#handleChatAfterSwap(event);
    this.#beforeSwapHandler = (event) => this.#handleChatBeforeSwap(event);
    this.#pageShowHandler = (event) => this.#handlePageShow(event);
    this.#historyRestoreHandler = (event) => this.#handleHistoryRestore(event);
  }

  get streamingSession() {
    return this.#session;
  }

  #setRenderingState(isRendering) {
    if (isRendering) {
      this.setAttribute("data-rendering", "true");
      this.setAttribute("aria-busy", "true");
    } else {
      this.removeAttribute("data-rendering");
      this.setAttribute("aria-busy", "false");
    }
  }

  #scheduleRenderingComplete(chat) {
    const finalize = () => {
      if (this.#chat === chat) {
        this.#setRenderingState(false);
        this.#queuePendingScrollTarget();
      }
    };

    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(finalize);
    });
  }

  #queuePendingScrollTarget() {
    if (!this.#pendingScrollTarget) {
      return;
    }

    window.requestAnimationFrame(() => {
      if (!this.#pendingScrollTarget) {
        return;
      }

      if (this.hasAttribute("data-rendering")) {
        this.#queuePendingScrollTarget();
        return;
      }

      this.#applyPendingScrollTarget();
    });
  }

  #applyPendingScrollTarget() {
    if (!this.#pendingScrollTarget) {
      return;
    }

    const chat = this.#chat;
    if (!chat || !this.isConnected) {
      return;
    }

    const isVisible = this.offsetParent !== null && chat.offsetParent !== null;
    if (!isVisible) {
      window.requestAnimationFrame(() => this.#applyPendingScrollTarget());
      return;
    }

    scrollToHighlight(this.#pendingScrollTarget);
    this.#pendingScrollTarget = null;
  }

  connectedCallback() {
    super.connectedCallback();
    if (!this.style.display) {
      this.style.display = "block";
    }

    this.setAttribute("aria-busy", this.hasAttribute("data-rendering") ? "true" : "false");

    this.#connectionListeners = this.resetListenerBag(this.#connectionListeners);
    this.#connectionListeners.add(window, "pageshow", this.#pageShowHandler);
    this.#connectionListeners.add(
      document.body,
      "htmx:historyRestore",
      this.#historyRestoreHandler
    );

    if (!this.#scrollManager) {
      this.#scrollManager = window.appInit?.scroll ?? null;
    }

    this.#scrollEventListeners = this.resetListenerBag(this.#scrollEventListeners);
    this.#scrollEventListeners.add(scrollEvents, "scroll:markdown-complete", () => {
      if (this.#chat) {
        this.#scheduleRenderingComplete(this.#chat);
      }
    });

    this.#syncToChatDate();
  }

  disconnectedCallback() {
    this.#cancelHistoryRestoreFrame();
    this.#teardown();
    this.#connectionListeners = this.disposeListenerBag(this.#connectionListeners);
    this.#scrollEventListeners = this.disposeListenerBag(this.#scrollEventListeners);
    this.#initialized = false;
    super.disconnectedCallback();
  }

  #initialize(
    chat = this.querySelector("#chat"),
    chatDate = chat?.dataset?.date ?? null
  ) {
    this.#initialized = false;
    this.#teardown();

    this.#pendingScrollTarget = this.dataset?.scrollTarget || null;

    setTimezoneCookie();

    if (!chat) {
      this.#setRenderingState(false);
      this.#chat = null;
      this.#lastRenderedDay = null;
      this.#forceNavFlash = false;
      clearActiveDay();
      return;
    }

    const container = document.getElementById("content-wrapper");

    const initialStreamMsgId = chat?.dataset?.currentStream || null;
    this.#sessionListeners = this.disposeListenerBag(this.#sessionListeners);
    this.#session = new StreamingSession({
      currentMsgId: initialStreamMsgId || null,
    });
    this.#sessionListeners = this.resetListenerBag(this.#sessionListeners);
    this.#sessionListeners.add(this.#session, "streaming:begin", (event) =>
      this.#onSessionBegin(event)
    );
    this.#sessionListeners.add(this.#session, "streaming:abort", (event) =>
      this.#onSessionAbort(event)
    );
    this.#sessionListeners.add(
      this.#session,
      "streaming:complete",
      (event) => this.#onSessionComplete(event)
    );

    this.#chat = chat;
    this.#syncChatDataset(initialStreamMsgId);

    if (this.#pendingScrollTarget) {
      this.#queuePendingScrollTarget();
    }

    const activeDay = chatDate || null;
    const activeDayLabel = chat?.dataset?.longDate ?? null;
    const viewKind = this.dataset?.viewKind || null;
    const updateClientToday = window?.appInit?.updateClientToday;
    const clientToday =
      typeof updateClientToday === "function"
        ? updateClientToday()
        : formatDate(new Date());

    if (typeof updateClientToday !== "function" && document?.body?.dataset) {
      document.body.dataset.clientToday = clientToday;
    }

    const isClientToday = activeDay === clientToday;

    if (viewKind === "today" && activeDay && !isClientToday) {
      this.#forceNavFlash = true;
      navigateToDate(clientToday);
      return;
    }

    this.#lastRenderedDay = activeDay;

    setActiveDay(activeDay, activeDayLabel, {
      detail: { source: "chat-view" },
    });

    chat.querySelectorAll?.(".markdown-body").forEach((el) => {
      if (el?.dataset?.rendered === "true") {
        return;
      }

      const activeStream = el?.closest?.("llm-stream[data-streaming='true']");
      if (activeStream) {
        return;
      }

      if (!el?.querySelector?.(TYPING_INDICATOR_SELECTOR)) {
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
        session: this.#session,
        date: activeDay,
      });
      this.#chatFormReady = chatFormReady.then(() => {
        if (this.#chatForm !== currentForm) return;
        const currentMsgId = this.#session?.currentMsgId ?? null;
        if (this.#chatForm) {
          this.#chatForm.streamingMsgId = currentMsgId;
        }
        if (typeof this.#chatForm.setStreaming === "function") {
          this.#chatForm.setStreaming(Boolean(currentMsgId));
        }
      });
    } else {
      this.#chatFormReady = Promise.resolve();
    }

    if (activeDay && activeDay === formatDate(new Date())) {
      this.#midnightCleanup = scheduleMidnightRefresh(chat);
    }

    if (!this.#scrollManager) {
      this.#scrollManager = window.appInit?.scroll ?? null;
    }
    this.#scrollManager?.attachChat(chat);

    this.#chatListeners = this.resetListenerBag(this.#chatListeners);
    this.#chatListeners.add(chat, "htmx:afterSwap", this.#afterSwapHandler);
    this.#chatListeners.add(chat, "htmx:beforeSwap", this.#beforeSwapHandler);

    activateAnimations(chat);

    this.#markdownObserver = new MarkdownObserver({
      root: chat,
      onRender: (el) => this.#handleMarkdownRendered(el),
    });
    this.#markdownObserver.start();
    chatFormReady.then(() => this.#applySessionState());

    const shouldForceNavFlash = this.#forceNavFlash;
    initDayNav(chat, {
      activeDay,
      label: activeDayLabel,
      forceFlash: shouldForceNavFlash,
    });
    if (shouldForceNavFlash) {
      this.#forceNavFlash = false;
    }

    if (activeDayLabel) {
      document.title = activeDayLabel;
    } else if (activeDay) {
      document.title = activeDay;
    }

    this.#initialized = true;
    this.#scheduleRenderingComplete(chat);
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

    if (this.#scrollManager && this.#chat) {
      this.#scrollManager.detachChat(this.#chat);
    }

    this.#markdownObserver?.stop();
    this.#markdownObserver = null;

    this.#chatListeners = this.disposeListenerBag(this.#chatListeners);
    this.#sessionListeners = this.disposeListenerBag(this.#sessionListeners);

    this.#chat = null;
    this.#chatForm = null;
    this.#session = null;
    this.#chatFormReady = Promise.resolve();
    this.#pendingScrollTarget = null;
  }

  #handleChatBeforeSwap(event) {
    if (!this.#chat || !this.#markdownObserver) return;

    const swapTargets = this.#collectSwapTargets(event);
    if (swapTargets.includes(this.#chat)) {
      this.#setRenderingState(true);
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

    if (swapTargets.includes(this.#chat)) {
      this.#applySessionState({ forceScroll: true });
      this.#scheduleRenderingComplete(this.#chat);
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
    this.#forceNavFlash = true;
    this.#cancelHistoryRestoreFrame();
    this.#historyRestoreFrame = window.requestAnimationFrame(() => {
      this.#historyRestoreFrame = null;
      this.#syncToChatDate();
      if (this.#chatForm) {
        const currentForm = this.#chatForm;
        const chatFormReady = this.#wireChatForm(currentForm, {
          chat: this.#chat,
          container: document.getElementById("content-wrapper"),
          session: this.#session,
          date: this.#lastRenderedDay,
        });
        this.#chatFormReady = chatFormReady.then(() => {
          if (this.#chatForm !== currentForm) return;
          const currentMsgId = this.#session?.currentMsgId ?? null;
          if (this.#chatForm) {
            this.#chatForm.streamingMsgId = currentMsgId;
          }
          if (typeof this.#chatForm.setStreaming === "function") {
            this.#chatForm.setStreaming(Boolean(currentMsgId));
          }
        });
        this.#chatFormReady.then(() => this.#applySessionState());
      } else {
        this.#chatFormReady = Promise.resolve();
      }
      this.#resumeDormantStreams();
    });
  }

  async #wireChatForm(chatForm, { chat, container, session, date }) {
    if (!chatForm) return;
    await customElements.whenDefined("chat-form");
    if (!chatForm.isConnected) return;
    customElements.upgrade(chatForm);
    chatForm.container = container;
    chatForm.chat = chat;
    chatForm.session = session;
    chatForm.date = date;
  }

  #cancelHistoryRestoreFrame() {
    if (this.#historyRestoreFrame != null) {
      window.cancelAnimationFrame(this.#historyRestoreFrame);
      this.#historyRestoreFrame = null;
    }
  }

  async #applySessionState({ msgId, streaming, forceScroll = false } = {}) {
    if (!this.#chatForm) return;

    await this.#chatFormReady;

    if (!this.#chatForm) return;

    const nextMsgId =
      msgId !== undefined ? msgId : this.#session?.currentMsgId || null;
    const nextStreaming =
      streaming !== undefined ? streaming : Boolean(nextMsgId);

    this.#chatForm.streamingMsgId = nextMsgId;
    if (typeof this.#chatForm.setStreaming === "function") {
      this.#chatForm.setStreaming(nextStreaming);
    }

    if (forceScroll) {
      this.#scrollManager?.scrollToBottom(true);
    }
  }

  #syncChatDataset(msgId) {
    if (!this.#chat) {
      return;
    }

    if (msgId) {
      this.#chat.dataset.currentStream = msgId;
    } else {
      delete this.#chat.dataset.currentStream;
    }
  }

  #handleMarkdownRendered(el) {
    const stream = el?.closest?.("llm-stream");
    stream?.handleMarkdownRendered(el);
  }

  #resumeDormantStreams() {
    if (!this.#chat) return;

    const resume = (stream) => {
      if (!stream || stream.dataset?.streaming !== "true") return;
      if (typeof stream.resume === "function") {
        stream.resume();
      }
    };

    if (this.#chat instanceof Element && this.#chat.matches("llm-stream")) {
      resume(this.#chat);
    }

    this.#chat.querySelectorAll?.("llm-stream").forEach((stream) => resume(stream));
  }

  #onSessionBegin(event) {
    const detail = event?.detail || {};
    const msgId = detail.userMsgId || null;
    this.#syncChatDataset(msgId);
    this.#applySessionState({ msgId, streaming: Boolean(msgId) });
    scrollEvents.dispatchEvent(
      new CustomEvent("scroll:force-bottom", {
        detail: { source: "stream:start" },
      })
    );
  }

  #onSessionAbort(event) {
    void event;
    this.#syncChatDataset(null);
    this.#applySessionState({ msgId: null, streaming: false });
  }

  #onSessionComplete(event) {
    const detail = event?.detail || {};
    const status = detail.status || "done";
    this.#syncChatDataset(null);
    this.#applySessionState({ msgId: null, streaming: false });
    if (status !== "aborted") {
      scrollEvents.dispatchEvent(
        new CustomEvent("scroll:force-bottom", {
          detail: { source: "stream:complete" },
        })
      );
    }
  }
}
