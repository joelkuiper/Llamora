import { createListenerBag } from "../utils/events.js";

export class ScrollController {
  constructor({
    root = document,
    chat,
    containerSelector = "#content-wrapper",
    buttonSelector = "#scroll-bottom",
  }) {
    this.root = root;
    this.chat = chat;
    this.container = root.querySelector(containerSelector);
    this.scrollBtn = root.querySelector(buttonSelector);
    this.scrollBtnContainer = this.scrollBtn?.parentElement || null;
    this.autoScrollEnabled = true;
    this.lastScrollTop = 0;
    this.listeners = null;

    this.alignScrollButton = this.alignScrollButton.bind(this);
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
    }

    requestAnimationFrame(() => {
      this.toggleScrollBtn();
      requestAnimationFrame(() => this.toggleScrollBtn());
    });

    this.alignScrollButton();

    return (force = false) => this.scrollToBottom(force);
  }

  destroy() {
    if (!this.container || !this.chat) return;

    this.listeners?.abort();
    this.listeners = null;
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
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
    if (!this.scrollBtn) return;
    if (this.isUserNearBottom(150)) {
      this.scrollBtn.classList.remove("visible");
      this.scrollBtnContainer?.classList.remove("visible");
    } else {
      this.scrollBtn.classList.add("visible");
      this.scrollBtnContainer?.classList.add("visible");
    }
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
    if (!this.chat) return;
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
    if (!this.scrollBtn) return;
    this.scrollBtn.classList.add("clicked");
    this.scrollToBottom(true);
    setTimeout(() => this.scrollBtn?.classList.remove("clicked"), 300);
  }
}
