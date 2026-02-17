import { appReady } from "../../app-init.js";
import { initDayNav, navigateToDate } from "../../day.js";
import { renderMarkdownInElement } from "../../markdown.js";
import { scrollEvents } from "../../scroll-manager.js";
import {
  formatTimeElements,
  getClientToday,
  getTimezone,
  scheduleMidnightRollover,
  updateClientToday,
} from "../../services/time.js";
import { TYPING_INDICATOR_SELECTOR } from "../../typing-indicator.js";
import { scrollToHighlight } from "../../ui.js";
import { ReactiveElement } from "../../utils/reactive-element.js";
import { afterNextFrame, scheduleFrame } from "../../utils/scheduler.js";
import { clearActiveDay, setActiveDay } from "./active-day-store.js";
import { armEntryAnimations, armInitialEntryAnimations } from "./entry-animations.js";
import { MarkdownObserver } from "./markdown-observer.js";
import { StreamController } from "./stream-controller.js";
import "../entry-form.js";
import "../response-stream.js";

const activateAnimations = armEntryAnimations;
const activateInitialEntryAnimations = armInitialEntryAnimations;

export class EntryView extends ReactiveElement {
  #entryForm = null;
  #scrollManager = null;
  #scrollEventListeners = null;
  #entries = null;
  #midnightCleanup = null;
  #afterSwapHandler;
  #beforeSwapHandler;
  #pageShowHandler;
  #historyRestoreHandler;
  #historyRestoreFrame = null;
  #connectionListeners = null;
  #entryListeners = null;
  #streamController = null;
  #markdownObserver = null;
  #initialized = false;
  #lastRenderedDay = null;
  // biome-ignore lint/correctness/noUnusedPrivateClassMembers: initialized via events elsewhere
  #entryFormReady = Promise.resolve();
  #pendingScrollTarget = null;
  #forceNavFlash = false;
  #appReadyPromise = null;

  constructor() {
    super();
    this.#afterSwapHandler = (event) => this.#handleEntriesAfterSwap(event);
    this.#beforeSwapHandler = (event) => this.#handleEntriesBeforeSwap(event);
    this.#pageShowHandler = (event) => this.#handlePageShow(event);
    this.#historyRestoreHandler = (event) => this.#handleHistoryRestore(event);
  }

  get streamController() {
    return this.#streamController;
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

  #scheduleRenderingComplete(entries) {
    const finalize = () => {
      if (this.#entries === entries) {
        this.#setRenderingState(false);
        this.#queuePendingScrollTarget();
      }
    };

    afterNextFrame(finalize);
  }

  #queuePendingScrollTarget() {
    if (!this.#pendingScrollTarget) {
      return;
    }

    scheduleFrame(() => {
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

    const entries = this.#entries;
    if (!entries || !this.isConnected) {
      return;
    }

    const isVisible = this.offsetParent !== null && entries.offsetParent !== null;
    if (!isVisible) {
      scheduleFrame(() => this.#applyPendingScrollTarget());
      return;
    }

    scrollToHighlight(this.#pendingScrollTarget);
    this.#pendingScrollTarget = null;
  }

  #observeAppReady() {
    if (this.#appReadyPromise) {
      return;
    }

    this.#appReadyPromise = appReady
      .catch(() => null)
      .then((app) => {
        if (!this.isConnected) {
          return;
        }

        const manager = app?.scroll ?? window.appInit?.scroll ?? null;
        if (manager && manager !== this.#scrollManager) {
          if (this.#scrollManager && this.#entries) {
            this.#scrollManager.detachEntries(this.#entries);
          }
          this.#scrollManager = manager;
          if (this.#entries) {
            this.#scrollManager.attachEntries(this.#entries);
          }
        }
      });
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
      this.#historyRestoreHandler,
    );

    this.#observeAppReady();

    if (!this.#scrollManager) {
      this.#scrollManager = window.appInit?.scroll ?? null;
    }

    this.#scrollEventListeners = this.resetListenerBag(this.#scrollEventListeners);
    this.#scrollEventListeners.add(scrollEvents, "scroll:markdown-complete", () => {
      if (this.#entries) {
        this.#scheduleRenderingComplete(this.#entries);
      }
    });

    this.#syncToEntryDate();
  }

  disconnectedCallback() {
    this.#cancelHistoryRestoreFrame();
    this.#teardown();
    this.#connectionListeners = this.disposeListenerBag(this.#connectionListeners);
    this.#scrollEventListeners = this.disposeListenerBag(this.#scrollEventListeners);
    this.#appReadyPromise = null;
    this.#initialized = false;
    super.disconnectedCallback();
  }

  #initialize(
    entries = this.querySelector("#entries"),
    entriesDate = entries?.dataset?.date ?? null,
  ) {
    this.#initialized = false;
    this.#teardown();

    this.#pendingScrollTarget = this.dataset?.scrollTarget || null;

    getTimezone();

    if (!entries) {
      this.#setRenderingState(false);
      this.#entries = null;
      this.#lastRenderedDay = null;
      this.#forceNavFlash = false;
      clearActiveDay();
      return;
    }

    const container = document.getElementById("content-wrapper");

    const _initialStreamMsgId = entries?.dataset?.currentStream || null;
    this.#streamController?.dispose();
    this.#streamController = new StreamController();
    this.#streamController.setEntries(entries);

    this.#entries = entries;

    if (this.#pendingScrollTarget) {
      this.#queuePendingScrollTarget();
    }

    const activeDay = entriesDate || null;
    const activeDayLabel = entries?.dataset?.longDate ?? null;
    const minDate = entries?.dataset?.minDate || null;
    const viewKind = this.dataset?.viewKind || null;
    const clientToday = updateClientToday() || getClientToday();

    const isClientToday = activeDay === clientToday;

    if (viewKind === "today" && activeDay && !isClientToday) {
      this.#forceNavFlash = true;
      navigateToDate(clientToday);
      return;
    }

    this.#lastRenderedDay = activeDay;

    setActiveDay(activeDay, activeDayLabel, {
      detail: { source: "entry-view" },
    });
    if (minDate && document?.body?.dataset) {
      document.body.dataset.minDate = minDate;
    }

    entries.querySelectorAll?.(".markdown-body").forEach((el) => {
      if (el?.dataset?.rendered === "true") {
        return;
      }

      const activeStream = el?.closest?.("response-stream[data-streaming='true']");
      if (activeStream) {
        return;
      }

      if (!el?.querySelector?.(TYPING_INDICATOR_SELECTOR)) {
        renderMarkdownInElement(el);
      }
    });
    formatTimeElements(entries);

    this.#entryForm = this.querySelector("entry-form");
    let entryFormReady = Promise.resolve();
    if (this.#entryForm) {
      const currentForm = this.#entryForm;
      entryFormReady = this.#wireEntryForm(currentForm, {
        entries,
        container,
        date: activeDay,
      });
      this.#entryFormReady = entryFormReady.then(() => {
        if (this.#entryForm !== currentForm) return;
        this.#streamController?.refresh();
      });
    } else {
      this.#entryFormReady = Promise.resolve();
    }

    if (isClientToday) {
      this.#midnightCleanup = scheduleMidnightRollover(entries);
    }

    if (!this.#scrollManager) {
      this.#scrollManager = window.appInit?.scroll ?? null;
    }
    this.#scrollManager?.attachEntries(entries);

    this.#entryListeners = this.resetListenerBag(this.#entryListeners);
    this.#entryListeners.add(entries, "htmx:afterSwap", this.#afterSwapHandler);
    this.#entryListeners.add(entries, "htmx:beforeSwap", this.#beforeSwapHandler);

    activateInitialEntryAnimations(entries);

    this.#markdownObserver = new MarkdownObserver({
      root: entries,
      onRender: (el) => this.#handleMarkdownRendered(el),
    });
    this.#markdownObserver.start();
    entryFormReady.then(() => this.#streamController?.refresh());

    const shouldForceNavFlash = this.#forceNavFlash;
    initDayNav(entries, {
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
    this.#scheduleRenderingComplete(entries);
  }

  #syncToEntryDate() {
    const entries = this.querySelector("#entries");
    const entriesDate = entries?.dataset?.date ?? null;
    const entriesChanged = entries !== this.#entries;
    const dateChanged = entriesDate !== this.#lastRenderedDay;

    if (!entries) {
      if (this.#initialized || this.#entries || this.#lastRenderedDay) {
        this.#initialize(null, null);
      }
      return;
    }

    if (!this.#initialized || entriesChanged || dateChanged) {
      this.#initialize(entries, entriesDate);
    }
  }

  #teardown() {
    if (this.#midnightCleanup) {
      this.#midnightCleanup();
      this.#midnightCleanup = null;
    }

    if (this.#scrollManager && this.#entries) {
      this.#scrollManager.detachEntries(this.#entries);
    }

    this.#markdownObserver?.stop();
    this.#markdownObserver = null;

    this.#entryListeners = this.disposeListenerBag(this.#entryListeners);
    this.#streamController?.dispose();
    this.#streamController = null;

    this.#entries = null;
    this.#entryForm = null;
    this.#entryFormReady = Promise.resolve();
    this.#pendingScrollTarget = null;
  }

  #handleEntriesBeforeSwap(event) {
    if (!this.#entries || !this.#markdownObserver) return;

    const swapTargets = this.#collectSwapTargets(event);
    if (swapTargets.includes(this.#entries)) {
      this.#setRenderingState(true);
      this.#markdownObserver.pause();
    }
  }

  #handleEntriesAfterSwap(event) {
    if (!this.#entries) return;

    const swapTargets = this.#collectSwapTargets(event);
    const shouldNotify = swapTargets.some(
      (target) => target === this.#entries || target?.classList?.contains?.("entry"),
    );

    swapTargets.forEach((target) => {
      if (target === this.#entries) {
        activateAnimations(target);
        return;
      }

      if (target?.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
        target.querySelectorAll?.(".entry").forEach((node) => {
          activateAnimations(node);
        });
        return;
      }

      if (target?.classList?.contains("entry")) {
        activateAnimations(target);
      }
    });
    swapTargets.forEach((target) => {
      if (!target) return;
      if (target === this.#entries) {
        formatTimeElements(target);
        return;
      }
      formatTimeElements(target);
    });

    this.#markdownObserver?.resume(swapTargets);

    if (swapTargets.includes(this.#entries)) {
      this.#streamController?.refresh();
      this.#scrollManager?.scrollToBottom(true);
      this.#scheduleRenderingComplete(this.#entries);
    }

    this.#syncToEntryDate();

    if (shouldNotify) {
      document.body.dispatchEvent(new CustomEvent("entries:changed"));
    }
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
      if (globalThis.htmx?.process) {
        globalThis.htmx.process(document.body);
      }
      this.#initialized = false;
      this.#syncToEntryDate();
    }
  }

  #handleHistoryRestore() {
    this.#initialized = false;
    this.#forceNavFlash = true;
    this.#cancelHistoryRestoreFrame();
    const frame = scheduleFrame(() => {
      if (this.#historyRestoreFrame !== frame) {
        return;
      }
      this.#historyRestoreFrame = null;
      this.#syncToEntryDate();
      if (this.#entryForm) {
        const currentForm = this.#entryForm;
        const entryFormReady = this.#wireEntryForm(currentForm, {
          entries: this.#entries,
          container: document.getElementById("content-wrapper"),
          date: this.#lastRenderedDay,
        });
        this.#entryFormReady = entryFormReady.then(() => {
          if (this.#entryForm !== currentForm) return;
          this.#streamController?.refresh();
        });
      } else {
        this.#entryFormReady = Promise.resolve();
      }
      this.#resumeDormantStreams();
    });
    this.#historyRestoreFrame = frame;
  }

  async #wireEntryForm(entryForm, { entries, container, date }) {
    if (!entryForm) return;
    await customElements.whenDefined("entry-form");
    if (!entryForm.isConnected) return;
    customElements.upgrade(entryForm);
    entryForm.container = container;
    entryForm.entries = entries;
    entryForm.date = date;
  }

  #cancelHistoryRestoreFrame() {
    if (this.#historyRestoreFrame) {
      this.#historyRestoreFrame.cancel?.();
      this.#historyRestoreFrame = null;
    }
  }

  #handleMarkdownRendered(el) {
    const stream = el?.closest?.("response-stream");
    stream?.handleMarkdownRendered(el);
  }

  #resumeDormantStreams() {
    if (!this.#entries) return;

    const resume = (stream) => {
      if (!stream || stream.dataset?.streaming !== "true") return;
      if (typeof stream.resume === "function") {
        stream.resume();
      }
    };

    if (this.#entries instanceof Element && this.#entries.matches("response-stream")) {
      resume(this.#entries);
    }

    this.#entries.querySelectorAll?.("response-stream").forEach((stream) => {
      resume(stream);
    });
  }
}
