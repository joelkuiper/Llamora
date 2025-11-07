import { createListenerBag } from "../utils/events.js";
import { motionSafeBehavior, prefersReducedMotion } from "../utils/motion.js";

export const scrollEvents = new EventTarget();

const FORCE_BOTTOM_EVENT = "scroll:force-bottom";
const TARGET_EVENT = "scroll:target";
const TARGET_CONSUMED_EVENT = "scroll:target-consumed";
const REFRESH_EVENT = "scroll:refresh";
const HISTORY_RESTORE_EVENT = "scroll:history-restore";
const MARKDOWN_COMPLETE_EVENT = "scroll:markdown-complete";

const DEFAULT_CONTAINER_SELECTOR = "#content-wrapper";
const DEFAULT_BUTTON_SELECTOR = "scroll-bottom-button, #scroll-bottom";
const STORAGE_PREFIX = "scroll-pos";
const MARKDOWN_EVENT = "markdown:rendered";

const raf = (fn) =>
  typeof requestAnimationFrame === "function"
    ? requestAnimationFrame(fn)
    : setTimeout(fn, 16);

export class ScrollManager {
  #initSuppressed = false;
  #initReleaseFrame = null;
  #alignFrame = null;
  #listeners = null;
  #contextListeners = null;
  #resizeObserver = null;
  #storageErrorLogged = false;
  #skipNextRestore = false;
  #waitingKey = null;
  #started = false;

  constructor({
    root = document,
    containerSelector = DEFAULT_CONTAINER_SELECTOR,
    buttonSelector = DEFAULT_BUTTON_SELECTOR,
  } = {}) {
    this.root = root;
    this.containerSelector = containerSelector;
    this.buttonSelector = buttonSelector;

    this.chat = null;
    this.container = null;
    this.scrollElement = null;
    this.scrollBtn = null;
    this.scrollBtnContainer = null;
    this.autoScrollEnabled = true;
    this.lastScrollTop = 0;

    this.alignScrollButton = this.alignScrollButton.bind(this);
    this.alignScrollButtonNow = this.alignScrollButtonNow.bind(this);
    this.onScroll = this.onScroll.bind(this);
    this.onWheel = this.onWheel.bind(this);
    this.onTouchMove = this.onTouchMove.bind(this);
    this.onScrollBtnClick = this.onScrollBtnClick.bind(this);
  }

  start() {
    if (this.#started) return;
    this.#started = true;

    this.#listeners = createListenerBag();
    const bag = this.#listeners;

    bag.add(scrollEvents, FORCE_BOTTOM_EVENT, () => this.scrollToBottom(true));
    bag.add(scrollEvents, TARGET_EVENT, (event) => {
      const detail = event?.detail || {};
      if (!detail || (!detail.id && !detail.element)) return;
      this.scrollToTarget(detail.id ?? detail.element, detail.options);
    });
    bag.add(scrollEvents, REFRESH_EVENT, () => {
      this.ensureElements();
      this.toggleScrollBtn();
    });
    bag.add(scrollEvents, TARGET_CONSUMED_EVENT, (event) => {
      const detail = event?.detail || {};
      if (detail.target) {
        this.#skipNextRestore = true;
      } else {
        this.restore();
      }
    });

    bag.add(document, MARKDOWN_EVENT, () => this.#handleMarkdownRendered());

    bag.add(window, "pageshow", (event) => {
      if (event.persisted) {
        this.#skipNextRestore = false;
        this.restore();
      }
    });

    bag.add(document.body, "htmx:beforeSwap", (evt) => this.#handleBeforeSwap(evt));
    bag.add(document.body, "htmx:load", (evt) => this.#handleLoad(evt));
    bag.add(document.body, "htmx:historyRestore", (evt) => {
      this.#emitHistoryRestore(evt);
      this.#handleLoad(evt);
    });

    this.ensureElements();

    const hasActiveDay = Boolean(document.body?.dataset?.activeDay);
    if (hasActiveDay) {
      this.restore();
      return;
    }

    const resumeRestore = () => {
      this.restore();
    };

    bag.add(document, "chat:active-day-ready", resumeRestore, { once: true });
  }

  stop() {
    this.detachChat();
    this.#listeners?.abort();
    this.#listeners = null;
    this.#started = false;
  }

  ensureElements() {
    this.ensureContainer();
    this.resolveScrollButton();
  }

  ensureContainer() {
    const next = this.root.querySelector(this.containerSelector);
    if (next === this.container) {
      return this.container;
    }

    if (this.container && this.container !== next && this.#contextListeners) {
      // listeners will be reset when attachChat() is called.
      this.#contextListeners.abort();
      this.#contextListeners = null;
    }

    this.container = next instanceof HTMLElement ? next : null;
    if (this.container && this.chat) {
      this.#attachContextListeners();
    }
    return this.container;
  }

  resolveScrollButton() {
    const element = this.root.querySelector(this.buttonSelector);
    if (element === this.scrollElement) {
      return;
    }

    if (this.scrollBtn) {
      this.scrollBtn.removeEventListener("click", this.onScrollBtnClick);
    }
    if (this.scrollElement && this.scrollElement !== element) {
      this.scrollElement.removeEventListener("click", this.onScrollBtnClick);
    }

    this.scrollElement = element instanceof HTMLElement ? element : null;
    this.scrollBtn = null;
    this.scrollBtnContainer = null;

    if (!this.scrollElement) {
      return;
    }

    if (this.scrollElement instanceof HTMLButtonElement) {
      this.scrollBtn = this.scrollElement;
    } else {
      const button = this.scrollElement.querySelector?.("button") ?? null;
      if (button instanceof HTMLButtonElement) {
        this.scrollBtn = button;
      }
    }

    if (this.scrollElement.matches?.("button")) {
      this.scrollBtn = this.scrollElement;
    }

    if (this.scrollElement instanceof HTMLElement && !this.scrollElement.matches("button")) {
      this.scrollBtnContainer = this.scrollElement;
    } else {
      this.scrollBtnContainer = this.scrollBtn?.parentElement || null;
    }

    if (!this.scrollBtn && this.scrollElement instanceof HTMLElement) {
      const button = this.scrollElement.querySelector?.("button");
      if (button instanceof HTMLButtonElement) {
        this.scrollBtn = button;
      }
    }

    if (this.scrollBtn) {
      this.scrollBtnContainer ??= this.scrollBtn.parentElement || null;
      this.scrollBtn.addEventListener("click", this.onScrollBtnClick);
    } else if (this.scrollElement) {
      this.scrollElement.addEventListener("click", this.onScrollBtnClick);
    }
  }

  attachChat(chat) {
    if (chat === this.chat) {
      this.toggleScrollBtn();
      return;
    }

    this.chat = chat || null;
    this.ensureElements();
    this.#attachContextListeners();
    this.autoScrollEnabled = this.isUserNearBottom();
    this.lastScrollTop = this.container?.scrollTop ?? 0;
    this.#initSuppressed = true;
    this.toggleScrollBtn();
    raf(() => this.toggleScrollBtn());
    this.#scheduleInitRelease();
    this.alignScrollButton();
  }

  detachChat(chat = null) {
    if (chat && chat !== this.chat) {
      return;
    }

    const shouldResetCenter = !chat || chat === this.chat;

    this.#cancelInitRelease();
    this.#cancelAlign();

    this.#contextListeners?.abort();
    this.#contextListeners = null;

    this.#resizeObserver?.disconnect();
    this.#resizeObserver = null;

    this.chat = null;

    if (shouldResetCenter) {
      document.documentElement?.style?.removeProperty?.("--chat-center");
    }
  }

  scrollToBottom(force = false) {
    this.ensureContainer();
    if (!this.container) return;

    if (force) {
      this.autoScrollEnabled = true;
    }

    if (this.autoScrollEnabled || force) {
      this.container.scrollTo({
        top: this.container.scrollHeight,
        behavior: motionSafeBehavior("smooth"),
      });
    }

    this.toggleScrollBtn();

    if (force) {
      this.alignScrollButtonNow();
    } else {
      this.alignScrollButton();
    }
  }

  scrollToTarget(target, options = {}) {
    let element = null;
    if (typeof target === "string") {
      element = document.getElementById(target);
    } else if (target instanceof HTMLElement) {
      element = target;
    }

    if (!element) return false;

    const { behavior = "smooth", block = "center" } = options || {};
    this.#skipNextRestore = true;
    element.scrollIntoView({
      behavior: motionSafeBehavior(behavior),
      block,
    });
    return true;
  }

  notifyTargetConsumed(target) {
    const detail = { target: target ?? null };
    const event = new CustomEvent(TARGET_CONSUMED_EVENT, { detail });
    scrollEvents.dispatchEvent(event);
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
    if (!this.scrollBtn && !this.scrollBtnContainer) {
      return;
    }

    if (this.#initSuppressed) {
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
    if (rect.width === 0) {
      return;
    }

    const centerPx = rect.left + rect.width / 2;
    document.documentElement.style.setProperty("--chat-center", `${centerPx}px`);
  }

  onScroll() {
    if (!this.container) return;
    this.updateScrollState(this.container.scrollTop);
    this.toggleScrollBtn();
    this.#safeSet(this.#getKey(), String(this.container.scrollTop));
  }

  onWheel(event) {
    if (event?.deltaY < 0) {
      this.autoScrollEnabled = false;
    }
  }

  onTouchMove() {
    if (!this.container) return;
    if (this.container.scrollTop < this.lastScrollTop) {
      this.autoScrollEnabled = false;
    }
    this.lastScrollTop = this.container.scrollTop;
  }

  onScrollBtnClick() {
    if (!this.scrollBtn && !this.scrollBtnContainer) return;
    if (!prefersReducedMotion()) {
      this.scrollBtnContainer?.pulse?.();
      if (this.scrollBtn && !this.scrollBtnContainer?.pulse) {
        this.scrollBtn.classList.add("clicked");
        window.setTimeout(
          () => this.scrollBtn?.classList.remove("clicked"),
          300
        );
      }
    }
    this.scrollToBottom(true);
  }

  #attachContextListeners() {
    this.ensureContainer();
    if (!this.container) return;

    this.#contextListeners?.abort();
    this.#contextListeners = createListenerBag();
    const bag = this.#contextListeners;

    bag.add(this.container, "scroll", this.onScroll);
    bag.add(this.container, "wheel", this.onWheel, { passive: true });
    bag.add(this.container, "touchmove", this.onTouchMove, { passive: true });

    bag.add(window, "resize", this.alignScrollButton);
    bag.add(window, "scroll", this.alignScrollButton, { passive: true });

    if (typeof ResizeObserver === "function" && this.chat) {
      this.#resizeObserver?.disconnect();
      this.#resizeObserver = new ResizeObserver(() => this.alignScrollButton());
      this.#resizeObserver.observe(this.chat);
    }
  }

  #scheduleInitRelease() {
    if (!this.container) return;

    this.#cancelInitRelease();

    const now =
      typeof performance !== "undefined" && typeof performance.now === "function"
        ? () => performance.now()
        : () => Date.now();

    const start = now();
    const frame = { raf: null };
    this.#initReleaseFrame = frame;

    const releaseWhenReady = () => {
      if (this.#initReleaseFrame !== frame) return;

      const nearBottom = this.isUserNearBottom(10);
      const timedOut = now() - start >= 500;

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

  #handleBeforeSwap(event) {
    const target = event?.detail?.target || event?.target || null;
    if (!target || !this.container) return;

    if (target === this.container) {
      this.#safeSet(this.#getKey(), String(this.container.scrollTop || 0));
      return;
    }

    if (target instanceof Element && this.container.id && target.id === this.container.id) {
      this.#safeSet(this.#getKey(), String(this.container.scrollTop || 0));
    }
  }

  #handleLoad(event) {
    const detail = event?.detail || {};
    const possibleSources = [detail.item, detail.target, event?.target];

    for (const source of possibleSources) {
      const wrapper = this.#resolveWrapperFromNode(source);
      if (wrapper) {
        this.ensureContainer();
        this.maybeRestore();
        return;
      }
    }
  }

  #resolveWrapperFromNode(node) {
    if (!node) return null;

    if (typeof DocumentFragment !== "undefined" && node instanceof DocumentFragment) {
      return node.querySelector?.(this.containerSelector) ?? null;
    }

    if (typeof Element !== "undefined" && node instanceof Element) {
      if (node.matches?.(this.containerSelector)) {
        return node;
      }
      return node.querySelector?.(this.containerSelector) ?? null;
    }

    return null;
  }

  #emitHistoryRestore(event) {
    const detail = {
      event,
      key: this.#getKey(),
    };
    scrollEvents.dispatchEvent(
      new CustomEvent(HISTORY_RESTORE_EVENT, {
        detail,
      })
    );
  }

  #handleMarkdownRendered() {
    const currentKey = this.#getKey();

    if (this.#waitingKey && this.#waitingKey !== currentKey) {
      this.#waitingKey = null;
      return;
    }

    if (this.#waitingKey && this.needsMarkdownRender()) {
      return;
    }

    if (!this.#waitingKey) {
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key: currentKey, restored: false },
        })
      );
      return;
    }

    const saved = this.#safeGet(this.#waitingKey);
    this.#waitingKey = null;

    if (saved !== null) {
      this.#applySavedScroll(saved);
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key: currentKey, restored: true },
        })
      );
    } else {
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key: currentKey, restored: false },
        })
      );
    }
  }

  needsMarkdownRender() {
    this.ensureContainer();
    if (!this.container) return false;
    const nodes = this.container.querySelectorAll?.(".message .markdown-body");
    if (!nodes || nodes.length === 0) return false;

    return Array.from(nodes).some((node) => {
      if (!(node instanceof Element)) return false;
      if (node.dataset.rendered === "true") return false;
      return !node.querySelector?.("#typing-indicator");
    });
  }

  save() {
    if (!this.container) return;
    this.#safeSet(this.#getKey(), String(this.container.scrollTop || 0));
  }

  restore() {
    this.ensureContainer();
    if (!this.container) return;

    const key = this.#getKey();
    const params = new URLSearchParams(window.location.search);
    const hasTarget =
      params.has("target") ||
      (window.location.hash && window.location.hash.startsWith("#msg-"));

    if (hasTarget) {
      return;
    }

    if (this.needsMarkdownRender()) {
      this.#waitForMarkdown(key);
      return;
    }

    const saved = this.#safeGet(key);
    if (saved !== null) {
      this.#applySavedScroll(saved);
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key, restored: true },
        })
      );
    } else {
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key, restored: false },
        })
      );
    }
  }

  maybeRestore() {
    if (this.#skipNextRestore) {
      this.#skipNextRestore = false;
      return;
    }
    this.restore();
  }

  #waitForMarkdown(key) {
    this.#waitingKey = key;
  }

  #applySavedScroll(saved) {
    const value = Number.parseInt(saved, 10);
    if (!Number.isFinite(value)) return;
    raf(() => {
      this.ensureContainer();
      if (!this.container) return;
      this.container.scrollTop = value;
      this.updateScrollState(this.container.scrollTop);
    });
  }

  #getKey() {
    const activeDay = document.body?.dataset?.activeDay;
    if (activeDay) {
      return `${STORAGE_PREFIX}-day-${activeDay}`;
    }
    return `${STORAGE_PREFIX}-path-${window.location.pathname}`;
  }

  #getStorage() {
    try {
      if (typeof window === "undefined") return null;
      return window.sessionStorage ?? null;
    } catch (error) {
      this.#logStorageError(error);
      return null;
    }
  }

  #safeSet(key, value) {
    try {
      const storage = this.#getStorage();
      storage?.setItem?.(key, value);
    } catch (error) {
      this.#logStorageError(error);
    }
  }

  #safeGet(key) {
    try {
      const storage = this.#getStorage();
      if (!storage?.getItem) return null;
      return storage.getItem(key);
    } catch (error) {
      this.#logStorageError(error);
      return null;
    }
  }

  #logStorageError(error) {
    if (this.#storageErrorLogged) return;
    this.#storageErrorLogged = true;
    if (typeof console !== "undefined" && console.warn) {
      console.warn("Scroll memory storage disabled", error);
    }
  }
}

