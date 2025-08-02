// Disable form after submit
const form = document.getElementById("chat-form");
const textarea = form.querySelector("textarea");
const button = form.querySelector("button");

function setFormEnabled(enabled) {
  textarea.disabled = !enabled;
  button.disabled = !enabled;
  if (enabled) textarea.focus();
}

// Disable on request
form.addEventListener("htmx:afterRequest", () => {
  setFormEnabled(false);
});

// Re-enable on SSE close (streaming finished)
document.body.addEventListener("htmx:sseMessage", (evt) => {
  if(evt.detail.type === "done") {
    setFormEnabled(true);
  }
});

// Scroll to bottom logic
let autoScrollEnabled = true;
const SCROLL_THRESHOLD = 10;
let lastScrollY = window.scrollY;

function isUserNearBottom() {
  const distanceFromBottom =
        document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
  return distanceFromBottom < SCROLL_THRESHOLD;
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

// Listen to scroll position
window.addEventListener("scroll", () => {
  disableAutoScrollIfScrollingUp(window.scrollY);
});

// Listen to mouse wheel up (trackpad or wheel)
window.addEventListener("wheel", (e) => {
  if (e.deltaY < 0) {
    autoScrollEnabled = false;
  }
}, { passive: true });

// Touch devices
window.addEventListener("touchmove", () => {
  if (window.scrollY < lastScrollY) {
    autoScrollEnabled = false;
  }
  lastScrollY = window.scrollY;
}, { passive: true });

// Scroll on SSE updates
document.body.addEventListener("htmx:sseMessage", (evt) => {
  if (evt.detail.type === "message") {
    scrollToBottom();
  }
});

// Scroll after user submits a message
document.getElementById("chat-box").addEventListener("htmx:afterSwap", () => {
  scrollToBottom();
});
