let chatUIInitialized = false;

export function initChatUI(root = document) {
  const form = root.querySelector("#chat-form");
  const textarea = form?.querySelector("textarea");
  const button = form?.querySelector("button");
  const chatBox = root.querySelector("#chat-box");

  console.log("initialized?", chatUIInitialized);

  if (!form || !textarea || !button || !chatBox) return;

  // Prevent reinitialization of global listeners
  if (!chatUIInitialized) {
    setupGlobalListeners();
    chatUIInitialized = true;
  }

  // Handle form state
  form.addEventListener("htmx:afterRequest", () => setFormEnabled(false));
  form.addEventListener("htmx:configRequest", (event) => {
    if (!textarea.value.trim()) {
      event.preventDefault();
      textarea.focus();
    }
  });

  const errors = document.getElementById("errors");
  errors?.addEventListener("htmx:afterSwap", () => {
    requestAnimationFrame(() => {
      if (document.querySelector("#errors .error-box")) {
        setFormEnabled(true);
      }
    });
  });

  chatBox.addEventListener("htmx:afterSwap", () => {
    scrollToBottom();
  });

  window.scrollTo({ top: document.documentElement.scrollHeight });
  textarea.focus();

  function setFormEnabled(enabled) {
    textarea.disabled = !enabled;
    button.disabled = !enabled;
    if (enabled) textarea.focus();
  }
}

// ---------------------------
// GLOBAL STATE (initialized once)
// ---------------------------
let autoScrollEnabled = true;
let lastScrollY = window.scrollY;
const SCROLL_THRESHOLD = 10;

function setupGlobalListeners() {
  // SSE message scroll
  document.body.addEventListener("htmx:sseMessage", (evt) => {
    if (evt.detail.type === "done") {
      enableChatForm();
    } else if (evt.detail.type === "message") {
      scrollToBottom();
    }
  });

  // Scroll logic
  window.addEventListener("scroll", () => {
    disableAutoScrollIfScrollingUp(window.scrollY);
  });

  window.addEventListener("wheel", (e) => {
    if (e.deltaY < 0) autoScrollEnabled = false;
  }, { passive: true });

  window.addEventListener("touchmove", () => {
    if (window.scrollY < lastScrollY) autoScrollEnabled = false;
    lastScrollY = window.scrollY;
  }, { passive: true });
}

function scrollToBottom() {
  if (autoScrollEnabled) {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
  }
}

function disableAutoScrollIfScrollingUp(currentY) {
  if (currentY < lastScrollY - 2) {
    autoScrollEnabled = false;
  } else if (isUserNearBottom()) {
    autoScrollEnabled = true;
  }
  lastScrollY = currentY;
}

function isUserNearBottom() {
  const distanceFromBottom =
    document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
  return distanceFromBottom < SCROLL_THRESHOLD;
}

function enableChatForm() {
  const form = document.getElementById("chat-form");
  if (!form) return;
  const textarea = form.querySelector("textarea");
  const button = form.querySelector("button");
  if (textarea) textarea.disabled = false;
  if (button) button.disabled = false;
  textarea?.focus();
}
