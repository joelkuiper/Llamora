import { createListenerBag } from "../utils/events.js";

export class ScrollController {
  #initSuppressed = false;
  #initReleaseFrame = null;
  #alignFrame = null;

  constructor({
    root = document,
    chat,
    containerSelector = "#content-wrapper",
    buttonSelector = "scroll-bottom-button, #scroll-bottom",
  }) {
    this.root = root;
    this.chat = chat;
    this.container = root.querySelector(containerSelector);
    this.scrollElement = root.querySelector(buttonSelector);
    this.scrollBtn =
      this.scrollElement?.button ??
      (typeof this.scrollElement?.querySelector === "function"
        ? this.scrollElement.querySelector("button")
        : null);
    if (!this.scrollBtn && this.scrollElement instanceof HTMLButtonElement) {
      this.scrollBtn = this.scrollElement;
    }
    this.scrollBtnContainer =
      this.scrollElement instanceof HTMLElement &&
      !this.scrollElement.matches("button")
        ? this.scrollElement
        : this.scrollBtn?.parentElement || null;
    this.autoScrollEnabled = true;
    this.lastScrollTop = 0;
    this.listeners = null;

    this.alignScrollButton = this.alignScrollButton.bind(this);
    this.alignScrollButtonNow = this.alignScrollButtonNow.bind(this);
    this.onScroll = this.onScroll.bind(this);
    this.onWheel = this.onWheel.bind(this);
    this.onTouchMove = this.onTouchMove.bind(this);
    this.onScrollBtnClick = this.onScrollBtnClick.bind(this);
  }

  init() {
    if (!this.container || !this.chat) {
      return () => {};
    }

    this.autoScrollEnabled = this.isUserNearBottom();
    this.lastScrollTop = this.container.scrollTop;
    this.#initSuppressed = true;

    this.listeners?.abort();
    this.listeners = createListenerBag();
    const bag = this.listeners;

    bag.add(this.container, "scroll", this.onScroll);
    bag.add(this.container, "wheel", this.onWheel, { passive: true });
    bag.add(this.container, "touchmove", this.onTouchMove, { passive: true });

    bag.add(window, "resize", this.alignScrollButton);
    bag.add(window, "scroll", this.alignScrollButton, { passive: true });
    this.resizeObserver = new ResizeObserver(this.alignScrollButton);
    this.resizeObserver.observe(this.chat);

    if (this.scrollBtn) {
      bag.add(this.scrollBtn, "click", this.onScrollBtnClick);
    } else if (this.scrollElement) {
      bag.add(this.scrollElement, "click", this.onScrollBtnClick);
    }

    this.toggleScrollBtn();
    requestAnimationFrame(() => this.toggleScrollBtn());

    this.#scheduleInitRelease();

    this.alignScrollButtonNow();

    return (force = false) => this.scrollToBottom(force);
  }

  destroy() {
    if (!this.container || !this.chat) return;

    this.#cancelInitRelease();
    this.#initSuppressed = false;
    this.#cancelAlign();

    this.listeners?.abort();
    this.listeners = null;
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.scrollElement = null;
    this.scrollBtn = null;
    this.scrollBtnContainer = null;
  }

  isUserNearBottom(threshold = 0) {
    if (!this.container) return true;
    const distance =
      this.container.scrollHeight -
      this.container.clientHeight -
      this.container.scrollTop;
    return distance < threshold;
  }

  toggleScrollBtn() {
    if (this.#initSuppressed) {
      // During HTMX swaps the container can report a zero scroll offset before
      // settling back to the persisted position. Delay showing the button until
      // the layout has stabilized and we're sure the view is pinned to the
      // bottom so it doesn't flash during initialization.
      if (typeof this.scrollBtnContainer?.setVisible === "function") {
        this.scrollBtnContainer.setVisible(false);
        return;
      }

      this.scrollBtn?.classList.remove("visible");
      this.scrollBtnContainer?.classList.remove("visible");
      return;
    }

    const shouldShow = !this.isUserNearBottom(150);
    if (typeof this.scrollBtnContainer?.setVisible === "function") {
      this.scrollBtnContainer.setVisible(shouldShow);
      return;
    }

    const action = shouldShow ? "add" : "remove";
    this.scrollBtn?.classList[action]("visible");
    this.scrollBtnContainer?.classList[action]("visible");
  }

  scrollToBottom(force = false) {
    if (!this.container) return;
    if (force) this.autoScrollEnabled = true;
    if (this.autoScrollEnabled || force) {
      this.container.scrollTo({
        top: this.container.scrollHeight,
        behavior: "smooth",
      });
    }
    this.toggleScrollBtn();
    if (force) {
      this.alignScrollButtonNow();
    } else {
      this.alignScrollButton();
    }
  }

  updateScrollState(currentTop) {
    if (!this.container) return;
    if (currentTop < this.lastScrollTop - 2) {
      this.autoScrollEnabled = false;
    } else if (this.isUserNearBottom(10)) {
      this.autoScrollEnabled = true;
    }
    this.lastScrollTop = currentTop;
  }

  alignScrollButton() {
    if (this.#alignFrame != null || !this.chat) {
      return;
    }

    if (typeof requestAnimationFrame !== "function") {
      this.alignScrollButtonNow();
      return;
    }

    this.#alignFrame = requestAnimationFrame(() => {
      this.#alignFrame = null;
      this.alignScrollButtonNow();
    });
  }

  alignScrollButtonNow() {
    if (!this.chat) return;

    if (this.#alignFrame != null && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(this.#alignFrame);
      this.#alignFrame = null;
    }

    const rect = this.chat.getBoundingClientRect();
    const centerPx = rect.left + rect.width / 2;
    document.documentElement.style.setProperty("--chat-center", `${centerPx}px`);
  }

  onScroll() {
    this.updateScrollState(this.container.scrollTop);
    this.toggleScrollBtn();
  }

  onWheel(e) {
    if (e.deltaY < 0) {
      this.autoScrollEnabled = false;
    }
  }

  onTouchMove() {
    if (this.container.scrollTop < this.lastScrollTop) {
      this.autoScrollEnabled = false;
    }
    this.lastScrollTop = this.container.scrollTop;
  }

  onScrollBtnClick() {
    if (!this.scrollBtn && !this.scrollBtnContainer) return;
    this.scrollBtnContainer?.pulse?.();
    if (this.scrollBtn && !this.scrollBtnContainer?.pulse) {
      this.scrollBtn.classList.add("clicked");
      window.setTimeout(() => this.scrollBtn?.classList.remove("clicked"), 300);
    }
    this.scrollToBottom(true);
  }

  #scheduleInitRelease() {
    if (!this.container) return;

    this.#cancelInitRelease();

    const now =
      typeof performance !== "undefined" &&
      typeof performance.now === "function"
        ? () => performance.now()
        : () => Date.now();
    const start = now();
    const frame = { raf: null };
    this.#initReleaseFrame = frame;

    const releaseWhenReady = () => {
      if (this.#initReleaseFrame !== frame) return;

      const nearBottom = this.isUserNearBottom(10);
      const timedOut = now() - start >= 500;

      // Wait for the HTMX swap to settle back near the previous scroll
      // position before letting the button become visible. Fall back to a
      // short timeout so manual scrolls that intentionally leave the bottom
      // still reveal the control.
      if (nearBottom || timedOut) {
        this.#initReleaseFrame = null;
        this.#initSuppressed = false;
        this.toggleScrollBtn();
        return;
      }

      frame.raf = requestAnimationFrame(releaseWhenReady);
    };

    frame.raf = requestAnimationFrame(releaseWhenReady);
  }

  #cancelInitRelease() {
    const frame = this.#initReleaseFrame;
    if (!frame) return;
    if (frame.raf != null) {
      cancelAnimationFrame(frame.raf);
    }
    this.#initReleaseFrame = null;
  }

  #cancelAlign() {
    if (this.#alignFrame != null && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(this.#alignFrame);
    }
    this.#alignFrame = null;
  }
}
