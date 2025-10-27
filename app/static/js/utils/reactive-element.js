import { createListenerBag } from "./events.js";

function isFunction(value) {
  return typeof value === "function";
}

export class ReactiveElement extends HTMLElement {
  #defaultBag = null;
  #bags = new Set();

  connectedCallback() {
    this.#ensureDefaultBag();
  }

  disconnectedCallback() {
    this.abortListeners();
  }

  addListener(target, type, handler, options) {
    if (!target || !isFunction(target.addEventListener)) return;
    const bag = this.#ensureDefaultBag();
    bag.add(target, type, handler, options);
  }

  createListenerBag() {
    const bag = createListenerBag();
    this.#bags.add(bag);
    return bag;
  }

  resetListenerBag(bag = null) {
    if (bag) {
      this.disposeListenerBag(bag);
    }
    const next = this.createListenerBag();
    if (!this.#defaultBag) {
      this.#defaultBag = next;
    }
    return next;
  }

  disposeListenerBag(bag = null) {
    if (!bag) return null;
    try {
      bag.abort();
    } catch (err) {
      /* no-op */
    }
    this.#bags.delete(bag);
    if (bag === this.#defaultBag) {
      this.#defaultBag = null;
    }
    return null;
  }

  abortListeners() {
    for (const bag of this.#bags) {
      try {
        bag.abort();
      } catch (err) {
        /* no-op */
      }
    }
    this.#bags.clear();
    this.#defaultBag = null;
  }

  watchHtmxRequests(target = this, options = {}) {
    const {
      within = this,
      onStart = null,
      onEnd = null,
      bag = null,
    } = options;
    const listeners = bag || this.createListenerBag();

    const isRelevant = (event) => {
      if (!within) return true;
      if (isFunction(within)) {
        return !!within.call(this, event);
      }
      const scope = within === "self" ? this : within;
      if (scope instanceof Document) {
        return true;
      }
      if (scope instanceof Element) {
        const origin = event?.target;
        return origin instanceof Element && scope.contains(origin);
      }
      return true;
    };

    const wrap = (callback) => {
      if (!isFunction(callback)) return null;
      return (event) => {
        if (isRelevant(event)) {
          callback.call(this, event);
        }
      };
    };

    const startHandler = wrap(onStart);
    const endHandler = wrap(onEnd);

    if (startHandler) {
      listeners.add(target, "htmx:beforeRequest", startHandler);
    }

    if (endHandler) {
      listeners.add(target, "htmx:afterRequest", endHandler);
      listeners.add(target, "htmx:sendError", endHandler);
      listeners.add(target, "htmx:responseError", endHandler);
    }

    return listeners;
  }

  #ensureDefaultBag() {
    if (this.#defaultBag) {
      return this.#defaultBag;
    }
    const bag = this.createListenerBag();
    this.#defaultBag = bag;
    return bag;
  }
}
