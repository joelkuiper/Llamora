document.body.addEventListener("htmx:load", () => {
  const form = document.getElementById("chat-form");
  const textarea = form?.querySelector("textarea");
  const button = form?.querySelector("button");

  function setFormEnabled(enabled) {
    textarea.disabled = !enabled;
    button.disabled = !enabled;
    if (enabled) textarea.focus();
  }

  if (form && textarea && button && !form.dataset.listenerAttached) {
    form.dataset.listenerAttached = "true";

    form.addEventListener("htmx:afterRequest", () => {
      setFormEnabled(false);
    });

    document.body.addEventListener("htmx:sseMessage", (evt) => {
      if (evt.detail.type === "done") {
        setFormEnabled(true);
      }
    });
  }

  setFormEnabled(true); // on page load


  const chatBox = document.getElementById("chat-box");
  if (chatBox && !chatBox.dataset.listenerAttached) {
    chatBox.dataset.listenerAttached = "true";

    // Scroll logic
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

    window.addEventListener("scroll", () => {
      disableAutoScrollIfScrollingUp(window.scrollY);
    });

    window.addEventListener("wheel", (e) => {
      if (e.deltaY < 0) {
        autoScrollEnabled = false;
      }
    }, { passive: true });

    window.addEventListener("touchmove", () => {
      if (window.scrollY < lastScrollY) {
        autoScrollEnabled = false;
      }
      lastScrollY = window.scrollY;
    }, { passive: true });

    document.body.addEventListener("htmx:sseMessage", (evt) => {
      if (evt.detail.type === "message") {
        scrollToBottom();
      }
    });

    chatBox.addEventListener("htmx:afterSwap", () => {
      scrollToBottom();
    });
  }
});
