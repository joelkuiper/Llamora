import { isNearBottom, isNearTop } from "./scroll-utils.js";
import { getViewState } from "./services/view-state.js";
import { TYPING_INDICATOR_SELECTOR } from "./typing-indicator.js";
import { createListenerBag } from "./utils/events.js";
import { motionSafeBehavior, prefersReducedMotion } from "./utils/motion.js";
import { scheduleFrame, scheduleRafLoop } from "./utils/scheduler.js";
import {
  applyEdgeMetrics,
  computeEdgeMetrics,
  normalizeEdgeDirection,
} from "./utils/scroll-edge.js";

export const scrollEvents = new EventTarget();

/**
 * All scroll helper events share a common detail contract so listeners can rely on
 * the same metadata regardless of the caller.
 *
 * Standard fields:
 * - `source`: string identifier for the originator. Defaults to "unspecified".
 * - `reason`: optional free-form string describing the trigger. Defaults to null.
 * - `emittedAt`: timestamp (ms) noting when the helper dispatched the event.
 *
 * Additional fields are provided per-event:
 * - `force`: `boolean` present on `scroll:force-edge` to request a hard scroll.
 * - `direction`: optional `up` or `down` for `scroll:force-edge` to request a direction.
 * - `id` / `element`: target identifier or element for `scroll:target` requests.
 * - `options`: scrollIntoView options supplied for target navigation.
 * - `target`: identifier consumed by listeners acknowledging a target request.
 */

export const FORCE_EDGE_EVENT = "scroll:force-edge";
export const TARGET_EVENT = "scroll:target";
export const TARGET_CONSUMED_EVENT = "scroll:target-consumed";
export const REFRESH_EVENT = "scroll:refresh";
export const HISTORY_RESTORE_EVENT = "scroll:history-restore";
export const MARKDOWN_COMPLETE_EVENT = "scroll:markdown-complete";

const SCROLL_DETAIL_BASE = Object.freeze({
  source: "unspecified",
  reason: null,
  emittedAt: 0,
});

const FORCE_EDGE_DETAIL_BASE = Object.freeze({
  ...SCROLL_DETAIL_BASE,
  force: false,
  direction: "down",
});

const TARGET_DETAIL_BASE = Object.freeze({
  ...SCROLL_DETAIL_BASE,
  id: null,
  element: null,
  options: null,
});

const TARGET_CONSUMED_DETAIL_BASE = Object.freeze({
  ...SCROLL_DETAIL_BASE,
  target: null,
});

const stampDetail = (detail, base) => {
  const payload = { ...base, ...detail };
  if (typeof payload.emittedAt !== "number" || Number.isNaN(payload.emittedAt)) {
    payload.emittedAt = Date.now();
  }
  return payload;
};

const emitScrollEvent = (eventName, baseDetail, detail = {}) => {
  const normalized = stampDetail(detail, baseDetail);
  scrollEvents.dispatchEvent(
    new CustomEvent(eventName, {
      detail: normalized,
    }),
  );
  return normalized;
};

export function requestScrollForceEdge(detail = {}) {
  const direction = normalizeEdgeDirection(detail?.direction, "down");
  return emitScrollEvent(FORCE_EDGE_EVENT, FORCE_EDGE_DETAIL_BASE, {
    ...detail,
    force: detail?.force === true,
    direction,
  });
}

export function requestScrollForceBottom(detail = {}) {
  return requestScrollForceEdge({
    ...detail,
    direction: "down",
  });
}

export function requestScrollTarget(target, options = null, detail = {}) {
  const baseDetail = {
    ...detail,
    id: null,
    element: null,
    options: options ?? null,
  };
  if (typeof target === "string") {
    baseDetail.id = target;
  } else if (target instanceof HTMLElement) {
    baseDetail.element = target;
  }

  return emitScrollEvent(TARGET_EVENT, TARGET_DETAIL_BASE, baseDetail);
}

export function requestScrollTargetConsumed(target, detail = {}) {
  return emitScrollEvent(TARGET_CONSUMED_EVENT, TARGET_CONSUMED_DETAIL_BASE, {
    ...detail,
    target: target ?? null,
  });
}

const DEFAULT_CONTAINER_SELECTOR = "#content-wrapper";
const DEFAULT_BUTTON_SELECTOR = "scroll-edge-button, #scroll-bottom, #scroll-top";
const STORAGE_PREFIX = "scroll-pos";
const _MARKDOWN_EVENT = "markdown:rendered";

export class ScrollManager {
  #initSuppressed = false;
  #initReleaseFrame = null;
  #alignFrame = null;
  #listeners = null;
  #contextListeners = null;
  #resizeObserver = null;
  #markdownWaitObserver = null;
  #markdownWaitTimeout = null;
  #storageErrorLogged = false;
  #skipNextRestore = false;
  #waitingMarkdown = null;
  #waitingToken = 0;
  #started = false;
  #strategies = new Map();

  constructor({
    root = document,
    containerSelector = DEFAULT_CONTAINER_SELECTOR,
    buttonSelector = DEFAULT_BUTTON_SELECTOR,
  } = {}) {
    this.root = root;
    this.containerSelector = containerSelector;
    this.buttonSelector = buttonSelector;
    this.containerSelectorOverride = null;

    this.entries = null;
    this.container = null;
    this.scrollElement = null;
    this.scrollBtn = null;
    this.scrollBtnContainer = null;
    this.edgeDirection = "down";
    this.edgeThreshold = 150;
    this.edgeOffset = 0;
    this.autoScrollEnabled = true;
    this.lastScrollTop = 0;

    this.alignScrollButton = this.alignScrollButton.bind(this);
    this.alignScrollButtonNow = this.alignScrollButtonNow.bind(this);
    this.onScroll = this.onScroll.bind(this);
    this.onWheel = this.onWheel.bind(this);
    this.onTouchMove = this.onTouchMove.bind(this);
    this.onScrollBtnClick = this.onScrollBtnClick.bind(this);
  }

  registerStrategy(id, strategy) {
    const key = String(id || "").trim();
    if (!key) return null;
    if (!strategy || typeof strategy !== "object") return null;
    this.#strategies.set(key, strategy);
    return () => this.unregisterStrategy(key);
  }

  unregisterStrategy(id) {
    const key = String(id || "").trim();
    if (!key) return;
    this.#strategies.delete(key);
  }

  #buildStrategyContext(extra = {}) {
    return {
      manager: this,
      container: this.container,
      entries: this.entries,
      key: this.#getKey(),
      view: getViewState()?.view || "diary",
      containerSelector: this.containerSelectorOverride || this.containerSelector,
      ...extra,
    };
  }

  #strategyMatches(strategy, context) {
    if (!strategy) return false;
    const views = strategy.view
      ? (Array.isArray(strategy.view) ? strategy.view : [strategy.view]).map((value) =>
          String(value || "").trim(),
        )
      : [];
    if (views.length && !views.includes(String(context.view || "").trim())) {
      return false;
    }
    const selector = String(strategy.containerSelector || "").trim();
    if (selector && selector !== String(context.containerSelector || "").trim()) {
      return false;
    }
    if (typeof strategy.matches === "function" && !strategy.matches(context)) {
      return false;
    }
    return true;
  }

  #runStrategyHook(hook, extra = {}) {
    if (!this.#strategies.size) return false;
    const context = this.#buildStrategyContext(extra);
    for (const strategy of this.#strategies.values()) {
      if (!this.#strategyMatches(strategy, context)) {
        continue;
      }
      const fn = strategy?.[hook];
      if (typeof fn !== "function") continue;
      try {
        const result = fn(context);
        if (result) return true;
      } catch (error) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("Scroll strategy error", error);
        }
      }
    }
    return false;
  }

  #hasActiveHighlight() {
    if (!this.container) return false;

    // flashHighlight() decorates the target element with either the
    // `highlight` class or a temporary `data-flash-timer-id` attribute while
    // the animation is in progress. When either marker is present we should
    // treat the highlight as active and avoid clobbering the scroll position.
    const highlight = this.container.querySelector?.("[data-flash-timer-id], .entry.highlight");
    return highlight instanceof HTMLElement;
  }

  start() {
    if (this.#started) return;
    this.#started = true;
    this.ensureElements();
  }

  stop() {
    this.detachEntries();
    this.#listeners?.abort();
    this.#listeners = null;
    this.#started = false;
  }

  ensureElements() {
    this.resolveScrollButton();
    this.ensureContainer();
  }

  ensureContainer() {
    const selector = this.containerSelectorOverride || this.containerSelector;
    const next = this.root.querySelector(selector);
    if (next === this.container) {
      return this.container;
    }

    if (this.container && this.container !== next && this.#contextListeners) {
      // listeners will be reset when attachEntries() is called.
      this.#contextListeners.abort();
      this.#contextListeners = null;
    }

    this.container = next instanceof HTMLElement ? next : null;
    if (this.container && (this.entries || this.scrollElement)) {
      this.#attachContextListeners();
    }
    return this.container;
  }

  resolveScrollButton() {
    const element = this.root.querySelector(this.buttonSelector);
    if (element === this.scrollElement) {
      this.refreshEdgeMetrics();
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
    this.containerSelectorOverride = null;

    if (!this.scrollElement) {
      return;
    }

    const override = String(this.scrollElement.dataset.edgeContainer || "").trim();
    if (override) {
      this.containerSelectorOverride = override;
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

    this.refreshEdgeMetrics();
  }

  attachEntries(entries) {
    if (entries === this.entries) {
      this.toggleScrollBtn();
      return;
    }

    this.entries = entries || null;
    this.ensureElements();
    this.#attachContextListeners();
    this.autoScrollEnabled = this.isUserNearBottom(0);
    this.lastScrollTop = this.container?.scrollTop ?? 0;
    this.#initSuppressed = true;
    this.toggleScrollBtn();
    scheduleFrame(() => this.toggleScrollBtn());
    this.#scheduleInitRelease();
    this.alignScrollButton();
  }

  detachEntries(entries = null) {
    if (entries && entries !== this.entries) {
      return;
    }

    const shouldResetCenter = !entries || entries === this.entries;

    this.#cancelInitRelease();
    this.#cancelAlign();

    this.#contextListeners?.abort();
    this.#contextListeners = null;

    this.#resizeObserver?.disconnect();
    this.#resizeObserver = null;
    this.#cancelMarkdownWait("detach-entries");

    this.entries = null;

    if (shouldResetCenter && this.scrollElement instanceof HTMLElement) {
      this.scrollElement.style.removeProperty("--scroll-edge-center");
    }
  }

  scrollToEdge(force = false, direction = this.edgeDirection) {
    this.ensureContainer();
    if (!this.container) return;

    const normalizedDirection = normalizeEdgeDirection(direction, this.edgeDirection);

    if (normalizedDirection === "down") {
      if (force) {
        this.autoScrollEnabled = true;
      }

      if (this.autoScrollEnabled || force) {
        this.container.scrollTo({
          top: this.container.scrollHeight,
          behavior: motionSafeBehavior("smooth"),
        });
      }
    } else {
      this.container.scrollTo({
        top: 0,
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

  scrollToBottom(force = false) {
    this.scrollToEdge(force, "down");
  }

  scrollToTop(force = false) {
    this.scrollToEdge(force, "up");
  }

  handleForceEdge(detail) {
    const meta = detail || {};
    const force = meta.force === true;
    const direction = normalizeEdgeDirection(meta.direction, this.edgeDirection);

    if (direction === "down" && !force && !this.autoScrollEnabled) {
      this.toggleScrollBtn();
      this.alignScrollButton();
      return;
    }

    this.scrollToEdge(force, direction);
  }

  handleTargetConsumed(detail) {
    const meta = detail || {};
    if (meta.target) {
      this.#skipNextRestore = true;
    } else {
      this.restore();
    }
  }

  handlePageShow(event) {
    if (!event?.persisted) return;
    this.ensureElements();
    if (!this.container) return;

    const currentTop = this.container.scrollTop || 0;
    const key = this.#getKey();
    const saved = this.#safeGet(key);
    const savedTop = saved != null ? Number.parseInt(saved, 10) : NaN;
    const shouldRestore =
      Number.isFinite(savedTop) && (currentTop === 0 || Math.abs(savedTop - currentTop) > 4);

    if (shouldRestore) {
      this.restore();
      return;
    }

    this.updateScrollState(currentTop);
    this.toggleScrollBtn();
    this.alignScrollButton();
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

  notifyTargetConsumed(target, detail = {}) {
    requestScrollTargetConsumed(target, detail);
  }

  isUserNearBottom(threshold = 0) {
    if (!this.container) return true;
    return isNearBottom(this.container, threshold);
  }

  isUserNearEdge(threshold = 0) {
    if (!this.container) return true;
    if (this.edgeDirection === "up") {
      return isNearTop(this.container, threshold);
    }
    return isNearBottom(this.container, threshold);
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

    const shouldShow = !this.isUserNearEdge(this.edgeThreshold || 0);
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
    if (this.edgeDirection === "down") {
      if (currentTop < this.lastScrollTop - 2) {
        this.autoScrollEnabled = false;
      } else if (this.isUserNearBottom(10)) {
        this.autoScrollEnabled = true;
      }
    }
    this.lastScrollTop = currentTop;
  }

  alignScrollButton() {
    if (this.#alignFrame != null) {
      return;
    }

    const frame = scheduleFrame(() => {
      if (this.#alignFrame !== frame) {
        return;
      }
      this.#alignFrame = null;
      this.alignScrollButtonNow();
    });
    this.#alignFrame = frame;
  }

  alignScrollButtonNow() {
    this.#cancelAlign();
    this.refreshEdgeMetrics();
  }

  onScroll() {
    if (!this.container) return;
    this.updateScrollState(this.container.scrollTop);
    this.toggleScrollBtn();
    if (!this.#runStrategyHook("save", { reason: "scroll" })) {
      this.#safeSet(this.#getKey(), String(this.container.scrollTop));
    }
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
        window.setTimeout(() => this.scrollBtn?.classList.remove("clicked"), 300);
      }
    }
    this.scrollToEdge(true, this.edgeDirection);
  }

  refreshEdgeMetrics() {
    if (!this.scrollElement) return;
    const metrics = computeEdgeMetrics({
      button: this.scrollElement,
      root: this.root,
      fallbackCenter: this.entries,
      fallbackDirection: this.edgeDirection,
    });
    this.edgeDirection = metrics.direction;
    this.edgeThreshold = metrics.threshold;
    this.edgeOffset = metrics.offset;
    applyEdgeMetrics(this.scrollElement, metrics);
    if (
      this.scrollElement instanceof HTMLElement &&
      this.scrollElement.dataset.direction !== metrics.direction
    ) {
      this.scrollElement.dataset.direction = metrics.direction;
    }
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

    if (typeof ResizeObserver === "function" && this.entries) {
      this.#resizeObserver?.disconnect();
      this.#resizeObserver = new ResizeObserver(() => {
        this.alignScrollButton();
        if (this.container) {
          this.updateScrollState(this.container.scrollTop);
          this.toggleScrollBtn();
        }
      });
      this.#resizeObserver.observe(this.entries);
    }
  }

  #scheduleInitRelease() {
    if (!this.container) return;

    this.#cancelInitRelease();

    const controller = scheduleRafLoop({
      timeoutMs: 500,
      callback: ({ timedOut, stop }) => {
        if (this.#initReleaseFrame !== controller) {
          stop();
          return false;
        }

        const nearBottom = this.isUserNearBottom(10);

        if (nearBottom || timedOut) {
          stop();
          if (this.#initReleaseFrame === controller) {
            this.#initReleaseFrame = null;
          }
          this.#initSuppressed = false;
          this.toggleScrollBtn();
          return false;
        }

        return true;
      },
    });

    this.#initReleaseFrame = controller;
  }

  #cancelInitRelease() {
    if (this.#initReleaseFrame) {
      this.#initReleaseFrame.cancel?.();
      this.#initReleaseFrame = null;
    }
  }

  #cancelAlign() {
    if (this.#alignFrame) {
      this.#alignFrame.cancel?.();
      this.#alignFrame = null;
    }
  }

  handleBeforeSwap(event) {
    const target = event?.detail?.target || event?.target || null;
    if (!target || !this.container) return;

    if (target === this.container) {
      if (
        !this.#runStrategyHook("beforeSwap", { reason: "beforeSwap", event }) &&
        !this.#runStrategyHook("save", { reason: "beforeSwap", event })
      ) {
        this.#safeSet(this.#getKey(), String(this.container.scrollTop || 0));
      }
      return;
    }

    if (target instanceof Element && this.container.id && target.id === this.container.id) {
      if (
        !this.#runStrategyHook("beforeSwap", { reason: "beforeSwap", event }) &&
        !this.#runStrategyHook("save", { reason: "beforeSwap", event })
      ) {
        this.#safeSet(this.#getKey(), String(this.container.scrollTop || 0));
      }
    }
  }

  handleAfterSwap(event) {
    this.#runStrategyHook("afterSwap", { reason: "afterSwap", event });
  }

  handleAfterSettle(event) {
    this.#runStrategyHook("afterSettle", { reason: "afterSettle", event });
  }

  handleLoad(event) {
    const detail = event?.detail || {};
    const possibleSources = [detail.item, detail.target, event?.target];
    this.ensureElements();
    this.toggleScrollBtn();
    this.alignScrollButton();

    if (this.#runStrategyHook("load", { reason: "load", event })) {
      return;
    }

    for (const source of possibleSources) {
      const wrapper = this.resolveWrapperFromNode(source);
      if (wrapper) {
        this.maybeRestore();
        return;
      }
    }
  }

  resolveWrapperFromNode(node) {
    if (!node) return null;

    if (typeof DocumentFragment !== "undefined" && node instanceof DocumentFragment) {
      return node.querySelector?.(this.containerSelectorOverride || this.containerSelector) ?? null;
    }

    if (typeof Element !== "undefined" && node instanceof Element) {
      const selector = this.containerSelectorOverride || this.containerSelector;
      if (node.matches?.(selector)) {
        return node;
      }
      return node.querySelector?.(selector) ?? null;
    }

    return null;
  }

  emitHistoryRestore(event) {
    const detail = {
      event,
      key: this.#getKey(),
    };
    scrollEvents.dispatchEvent(
      new CustomEvent(HISTORY_RESTORE_EVENT, {
        detail,
      }),
    );
  }

  handleHistoryRestore(event) {
    return this.#runStrategyHook("historyRestore", { reason: "historyRestore", event });
  }

  handleMarkdownRendered(event) {
    const pending = this.#waitingMarkdown;
    const currentKey = this.#getKey();

    if (!pending) {
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key: currentKey, restored: false },
        }),
      );
      return;
    }

    const renderedElement = event?.detail?.element;
    if (
      renderedElement instanceof Element &&
      this.container &&
      !this.container.contains(renderedElement)
    ) {
      return;
    }

    this.#resolveMarkdownWait(pending.token, "markdown-rendered");
  }

  needsMarkdownRender() {
    this.ensureContainer();
    if (!this.container) return false;
    const nodes = this.container.querySelectorAll?.(".entry .markdown-body");
    if (!nodes || nodes.length === 0) return false;

    return Array.from(nodes).some((node) => {
      if (!(node instanceof Element)) return false;
      if (node.dataset.rendered === "true") return false;
      return !node.querySelector?.(TYPING_INDICATOR_SELECTOR);
    });
  }

  save() {
    if (!this.container) return;
    if (!this.#runStrategyHook("save", { reason: "save" })) {
      this.#safeSet(this.#getKey(), String(this.container.scrollTop || 0));
    }
  }

  restore() {
    this.ensureContainer();
    if (!this.container) return;

    if (this.#runStrategyHook("restore", { reason: "restore" })) {
      return;
    }

    if (this.#hasActiveHighlight()) {
      return;
    }

    const key = this.#getKey();
    const params = new URLSearchParams(window.location.search);
    const hasTarget = params.has("target") || window.location.hash?.startsWith("#entry-");

    if (hasTarget) {
      return;
    }

    if (this.needsMarkdownRender()) {
      this.#waitForMarkdown(key);
      return;
    }

    this.#cancelMarkdownWait("restore-immediate");

    const saved = this.#safeGet(key);
    if (saved !== null) {
      this.#applySavedScroll(saved);
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key, restored: true },
        }),
      );
    } else {
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key, restored: false },
        }),
      );
    }
  }

  maybeRestore() {
    if (this.#skipNextRestore) {
      this.#skipNextRestore = false;
      return;
    }
    if (this.#hasActiveHighlight()) {
      return;
    }
    this.restore();
  }

  #waitForMarkdown(key) {
    const token = ++this.#waitingToken;
    this.#waitingMarkdown = { key, token };
    this.#connectMarkdownWaitObserver(token);
  }

  #connectMarkdownWaitObserver(token) {
    this.#disconnectMarkdownWaitObserver();

    if (typeof ResizeObserver !== "function") return;

    const target = this.entries || this.container;
    if (!(target instanceof Element)) return;

    this.#markdownWaitObserver = new ResizeObserver(() => {
      this.#resolveMarkdownWait(token, "resize-observer");
    });
    this.#markdownWaitObserver.observe(target);
  }

  #disconnectMarkdownWaitObserver() {
    this.#markdownWaitObserver?.disconnect();
    this.#markdownWaitObserver = null;
    if (this.#markdownWaitTimeout) {
      window.clearTimeout(this.#markdownWaitTimeout);
      this.#markdownWaitTimeout = null;
    }
  }

  #cancelMarkdownWait(_reason = "cancel") {
    this.#waitingMarkdown = null;
    this.#disconnectMarkdownWaitObserver();
  }

  #resolveMarkdownWait(expectedToken, _source = "unknown") {
    const pending = this.#waitingMarkdown;
    if (!pending) return;
    if (pending.token !== expectedToken) return;

    const currentKey = this.#getKey();
    if (pending.key !== currentKey) {
      this.#cancelMarkdownWait("key-mismatch");
      return;
    }

    if (this.needsMarkdownRender()) {
      return;
    }

    const saved = this.#safeGet(pending.key);
    this.#cancelMarkdownWait("resolved");

    if (saved !== null) {
      this.#applySavedScroll(saved);
      scrollEvents.dispatchEvent(
        new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
          detail: { key: currentKey, restored: true },
        }),
      );
      return;
    }

    scrollEvents.dispatchEvent(
      new CustomEvent(MARKDOWN_COMPLETE_EVENT, {
        detail: { key: currentKey, restored: false },
      }),
    );
  }

  #applySavedScroll(saved) {
    const value = Number.parseInt(saved, 10);
    if (!Number.isFinite(value)) return;
    scheduleFrame(() => {
      this.ensureContainer();
      if (!this.container) return;
      this.container.scrollTop = value;
      this.updateScrollState(this.container.scrollTop);
    });
  }

  #getKey() {
    const entryDay = this.entries?.dataset?.date || null;
    if (entryDay) {
      return `${STORAGE_PREFIX}-day-${entryDay}`;
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
