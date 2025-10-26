import { renderAllMarkdown } from "./markdown.js";
import { initTagPopovers } from "./meta-chips.js";
import { setTimezoneCookie } from "./timezone.js";
import { ChatFormController } from "./chat/form-controller.js";
import { ScrollController } from "./chat/scroll-controller.js";
import { StreamController } from "./chat/stream-controller.js";

const TYPING_INDICATOR_SELECTOR = "#typing-indicator";

function findCurrentMsgId(chat) {
  const indicator = chat.querySelector(TYPING_INDICATOR_SELECTOR);
  if (!indicator) return null;
  return indicator.dataset.userMsgId || "opening";
}

export function refreshAtMidnight() {
  const chat = document.getElementById("chat");
  if (!chat) return;

  const pad = (n) => String(n).padStart(2, "0");

  const check = () => {
    const now = new Date();
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    if (chat.dataset.date !== today) {
      setTimezoneCookie();
      location.href = "/d/today";
    } else {
      const nextMidnight = new Date(now);
      nextMidnight.setHours(24, 0, 0, 0);
      setTimeout(check, nextMidnight.getTime() - now.getTime());
    }
  };

  if (!window.__refreshMidnightInit) {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") check();
    });
    window.__refreshMidnightInit = true;
  }

  check();
}

export function initChatUI(root = document) {
  setTimezoneCookie();
  const chat = root.querySelector("#chat");
  const container = root.querySelector("#content-wrapper");
  if (!chat) return null;

  const previous = document.body.__chatUIInstance;
  previous?.destroy?.();

  chat.querySelectorAll(".meta-chips").forEach((chips) => {
    delete chips.dataset.popInit;
  });

  const state = {
    currentStreamMsgId: null,
  };

  const formController = new ChatFormController({
    root,
    chat,
    container,
    date: chat.dataset.date,
    state,
  });
  formController.init();

  if (formController.isToday) {
    refreshAtMidnight();
  }

  const scrollController = new ScrollController({ root, chat });
  const scrollToBottom = scrollController.init() || (() => {});

  const streamController = new StreamController({
    chat,
    state,
    setStreaming: (streaming) => formController.setStreaming(streaming),
    scrollToBottom,
  });
  streamController.init();

  let observer;

  const instance = {
    destroy() {
      streamController.destroy();
      scrollController.destroy();
      formController.destroy();
      observer?.disconnect();
      observer = null;
      chat.removeEventListener("htmx:afterSwap", onAfterSwap);
      if (document.body.__chatUIInstance === instance) {
        document.body.__chatUIInstance = null;
      }
    },
    state,
    formController,
    scrollToBottom,
  };

  document.body.__chatUIInstance = instance;

  const updateStreamingState = (forceScroll = false) => {
    state.currentStreamMsgId = findCurrentMsgId(chat);
    if (state.currentStreamMsgId) {
      formController.setStreaming(true);
    } else {
      formController.setStreaming(false);
    }
    if (forceScroll) {
      scrollToBottom(true);
    }
  };

  const onAfterSwap = (event) => {
    renderAllMarkdown(chat);
    initTagPopovers(chat);
    if (event.target === chat) {
      updateStreamingState(true);
    }
  };
  chat.addEventListener("htmx:afterSwap", onAfterSwap);

  observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      m.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) {
          node.classList?.remove("no-anim");
          node.querySelectorAll?.(".no-anim").forEach((el) =>
            el.classList.remove("no-anim")
          );
        }
      });
    }
  });
  observer.observe(chat, { childList: true });

  renderAllMarkdown(chat);
  initTagPopovers(chat);
  updateStreamingState();

  return instance;
}
