import { createListenerBag } from "../utils/events.js";
import { scheduleFrame } from "../utils/scheduler.js";
import {
  scrollEvents,
  FORCE_BOTTOM_EVENT,
  TARGET_EVENT,
  TARGET_CONSUMED_EVENT,
  REFRESH_EVENT,
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

    bag.add(scrollEvents, FORCE_BOTTOM_EVENT, (event) => {
      manager.handleForceBottom(event?.detail);
    });
    bag.add(scrollEvents, TARGET_EVENT, (event) => {
      const detail = event?.detail || {};
      if (!detail || (!detail.id && !detail.element)) return;
      manager.scrollToTarget(detail.id ?? detail.element, detail.options);
    });
    bag.add(scrollEvents, REFRESH_EVENT, () => {
      manager.ensureElements();
      scheduleFrame(() => manager.toggleScrollBtn());
    });
    bag.add(scrollEvents, TARGET_CONSUMED_EVENT, (event) => {
      manager.handleTargetConsumed(event?.detail || {});
    });

    bag.add(document, MARKDOWN_EVENT, () => manager.handleMarkdownRendered());

    bag.add(window, "pageshow", (event) => {
      manager.handlePageShow(event);
    });

    bag.add(document.body, "htmx:beforeSwap", (evt) => manager.handleBeforeSwap(evt));
    bag.add(document.body, "htmx:load", (evt) => manager.handleLoad(evt));
    bag.add(document.body, "htmx:historyRestore", (evt) => {
      manager.emitHistoryRestore(evt);
      manager.handleLoad(evt);
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
