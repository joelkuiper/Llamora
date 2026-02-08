import { createListenerBag } from "./events.js";

function isFunction(value) {
  return typeof value === "function";
}

export function isEventWithin(event, scope) {
  if (!scope) {
    return false;
  }

  if (scope === event?.target || scope === event?.detail?.target) {
    return true;
  }

  const contains = typeof scope.contains === "function" ? scope.contains : null;
  if (!contains) {
    return false;
  }

  const target = event?.target;
  if (target instanceof Element && contains.call(scope, target)) {
    return true;
  }

  const detailTarget = event?.detail?.target;
  if (detailTarget instanceof Element && contains.call(scope, detailTarget)) {
    return true;
  }

  const detailElement = event?.detail?.elt;
  if (detailElement instanceof Element && contains.call(scope, detailElement)) {
    return true;
  }

  return false;
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
    } catch (_err) {
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
      } catch (_err) {
        /* no-op */
      }
    }
    this.#bags.clear();
    this.#defaultBag = null;
  }

  watchHtmxRequests(target = this, options = {}) {
    const {
      within = this,
      withinSelector = null,
      withinSelectors = null,
      onStart = null,
      onEnd = null,
      bag = null,
    } = options;
    const listeners = bag || this.createListenerBag();

    const selectors = [];
    if (Array.isArray(withinSelector)) {
      selectors.push(...withinSelector);
    } else if (withinSelector) {
      selectors.push(withinSelector);
    }
    if (Array.isArray(withinSelectors)) {
      selectors.push(...withinSelectors);
    } else if (withinSelectors) {
      selectors.push(withinSelectors);
    }

    const findBySelector = (selector) => {
      if (!selector || typeof selector !== "string") {
        return null;
      }
      const local = isFunction(this.querySelector) ? this.querySelector(selector) : null;
      if (local) {
        return local;
      }
      const root = this.getRootNode?.();
      if (root && typeof root.querySelector === "function") {
        const fromRoot = root.querySelector(selector);
        if (fromRoot) {
          return fromRoot;
        }
      }
      const doc = this.ownerDocument ?? document;
      return typeof doc.querySelector === "function" ? doc.querySelector(selector) : null;
    };

    const createScopeResolver = (value) => {
      if (!value) {
        return null;
      }
      if (value === "self") {
        return () => this;
      }
      if (value === "document") {
        return () => this.ownerDocument ?? document;
      }
      if (typeof value === "string") {
        return () => findBySelector(value);
      }
      if (typeof value.contains === "function") {
        return () => value;
      }
      return null;
    };

    const predicates = [];

    const registerScope = (value) => {
      if (!value) {
        return;
      }
      if (Array.isArray(value)) {
        for (const entry of value) {
          registerScope(entry);
        }
        return;
      }
      if (isFunction(value)) {
        predicates.push((event) => !!value.call(this, event));
        return;
      }
      const resolver = createScopeResolver(value);
      if (!resolver) {
        return;
      }
      predicates.push((event) => {
        const scope = resolver();
        return scope ? isEventWithin(event, scope) : false;
      });
    };

    registerScope(within);

    for (const selector of selectors) {
      predicates.push((event) => {
        const scope = findBySelector(selector);
        return scope ? isEventWithin(event, scope) : false;
      });
    }

    const isRelevant = (event) => {
      if (!predicates.length) {
        return true;
      }
      for (const predicate of predicates) {
        try {
          if (predicate(event)) {
            return true;
          }
        } catch (_err) {
          /* no-op */
        }
      }
      return false;
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
