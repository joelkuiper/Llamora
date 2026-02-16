import { createListenerBag } from "./utils/events.js";
import { scheduleFrame } from "./utils/scheduler.js";
import {
  FORCE_EDGE_EVENT,
  REFRESH_EVENT,
  scrollEvents,
  TARGET_CONSUMED_EVENT,
  TARGET_EVENT,
} from "./scroll-manager.js";

const MARKDOWN_EVENT = "markdown:rendered";

export class ScrollIntent {
  #manager = null;
  #listeners = null;
  #started = false;

  constructor(manager) {
    this.#manager = manager || null;
  }

  start() {
    if (this.#started || !this.#manager) return;
    this.#started = true;

    this.#listeners = createListenerBag();
    const bag = this.#listeners;
    const manager = this.#manager;
    const scrollPolicies = {
      [FORCE_EDGE_EVENT]: (event) => manager.handleForceEdge(event?.detail),
      [TARGET_EVENT]: (event) => {
        const detail = event?.detail || {};
        if (!detail || (!detail.id && !detail.element)) return;
        manager.scrollToTarget(detail.id ?? detail.element, detail.options);
      },
      [REFRESH_EVENT]: () => {
        manager.ensureElements();
        scheduleFrame(() => manager.toggleScrollBtn());
      },
      [TARGET_CONSUMED_EVENT]: (event) => {
        manager.handleTargetConsumed(event?.detail || {});
      },
    };
    const documentPolicies = {
      [MARKDOWN_EVENT]: () => manager.handleMarkdownRendered(),
    };
    const windowPolicies = {
      pageshow: (event) => manager.handlePageShow(event),
    };
    const bodyPolicies = {
      "htmx:beforeSwap": (evt) => manager.handleBeforeSwap(evt),
      "htmx:load": (evt) => manager.handleLoad(evt),
      "htmx:historyRestore": (evt) => {
        manager.emitHistoryRestore(evt);
        manager.handleLoad(evt);
      },
    };

    Object.entries(scrollPolicies).forEach(([eventName, handler]) => {
      bag.add(scrollEvents, eventName, handler);
    });
    Object.entries(documentPolicies).forEach(([eventName, handler]) => {
      bag.add(document, eventName, handler);
    });
    Object.entries(windowPolicies).forEach(([eventName, handler]) => {
      bag.add(window, eventName, handler);
    });
    Object.entries(bodyPolicies).forEach(([eventName, handler]) => {
      bag.add(document.body, eventName, handler);
    });

    manager.ensureElements();
    manager.restore();
  }

  stop() {
    if (!this.#started) return;
    this.#listeners?.abort();
    this.#listeners = null;
    this.#started = false;
  }
}
