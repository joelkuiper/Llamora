import { prefersReducedMotion } from "./motion.js";

const DEFAULT_ANIMATION_TIMEOUT = 250;

/**
 * Run a CSS animation by adding a class. Returns a Promise that resolves
 * when the animation completes (via animationend or safety timeout).
 *
 * Under prefers-reduced-motion, resolves immediately without animating.
 *
 * @param {Element} el
 * @param {string} className - CSS class that triggers the animation
 * @param {object} [opts]
 * @param {string[]} [opts.remove] - classes to remove before adding the new one
 * @param {number} [opts.timeout] - safety timeout in ms (0 to disable)
 */
export function playAnimation(
  el,
  className,
  { remove = [], timeout = DEFAULT_ANIMATION_TIMEOUT } = {},
) {
  if (!el) return Promise.resolve();

  for (const cls of remove) el.classList.remove(cls);

  if (prefersReducedMotion()) {
    return Promise.resolve();
  }

  void el.offsetHeight;
  el.classList.add(className);

  return new Promise((resolve) => {
    let done = false;
    const cleanup = () => {
      if (done) return;
      done = true;
      el.classList.remove(className);
      resolve();
    };
    el.addEventListener("animationend", cleanup, { once: true });
    if (timeout > 0) setTimeout(cleanup, timeout);
  });
}

/**
 * Show a hidden element with a CSS transition.
 * Removes the hidden attribute, forces a reflow, then adds the active class
 * in the next animation frame so the browser can paint the initial state first.
 *
 * @param {Element} el
 * @param {string} activeClass - CSS class that drives the visible state
 */
export function transitionShow(el, activeClass) {
  if (!el) return;
  el.hidden = false;
  void el.offsetHeight;
  requestAnimationFrame(() => el.classList.add(activeClass));
}

/**
 * Hide an element after its CSS transition completes.
 * Removes the active class immediately (starting the CSS transition),
 * then sets hidden=true after the specified duration.
 *
 * Returns a cancel function to abort the pending hide.
 *
 * @param {Element} el
 * @param {string|null} activeClass - CSS class to remove (null to skip)
 * @param {number} [durationMs=200] - time to wait for the CSS transition
 * @returns {() => void} cancel function
 */
export function transitionHide(el, activeClass, durationMs = 200) {
  if (!el) return () => {};
  if (activeClass) el.classList.remove(activeClass);
  let id = setTimeout(() => {
    id = null;
    el.hidden = true;
  }, durationMs);
  return () => {
    if (id !== null) {
      clearTimeout(id);
      id = null;
    }
  };
}

/**
 * Run a CSS animation with fine-grained lifecycle callbacks.
 * Returns a cancel function (call it to abort early and fire onCancel).
 *
 * Under prefers-reduced-motion, skips the animation and calls the
 * reducedMotion callback (if provided) or fires onFinish immediately.
 *
 * Compared to playAnimation, this gives imperative cancel control and
 * per-phase callbacks rather than a Promise.
 *
 * @param {Element} element
 * @param {string} className
 * @param {object} [options]
 * @param {Function} [options.onStart]
 * @param {Function} [options.onFinish]
 * @param {Function} [options.onCancel]
 * @param {boolean} [options.removeClass=true] - remove class when done
 * @param {Function} [options.reducedMotion] - (el, done) callback for reduced-motion
 * @param {number} [options.timeout=0] - safety timeout in ms (0 to disable)
 * @returns {() => void} cancel function
 */
export function animateMotion(element, className, options = {}) {
  const target = element ?? null;
  if (!target || typeof className !== "string" || className.length === 0) {
    return () => {};
  }

  const {
    onStart = null,
    onFinish = null,
    onCancel = null,
    removeClass = true,
    reducedMotion = null,
    timeout = 0,
  } = options;

  if (prefersReducedMotion()) {
    let doneCalled = false;
    const finish = () => {
      if (doneCalled) return;
      doneCalled = true;
      if (typeof onFinish === "function") {
        onFinish(target);
      }
    };

    let cleanup = null;
    if (typeof reducedMotion === "function") {
      cleanup = reducedMotion(target, finish) || null;
    } else {
      finish();
    }

    return () => {
      if (typeof cleanup === "function") {
        cleanup(target);
      }
      if (!doneCalled && typeof onCancel === "function") {
        onCancel(target);
      }
    };
  }

  if (typeof onStart === "function") {
    onStart(target);
  }

  let settled = false;

  const settle = (callback) => {
    if (settled) return;
    settled = true;
    target.removeEventListener("animationend", handleEnd);
    if (removeClass) {
      target.classList.remove(className);
    }
    if (typeof callback === "function") {
      callback(target);
    }
  };

  const handleEnd = (event) => {
    if (event?.target !== target) {
      return;
    }
    settle(onFinish);
  };

  target.addEventListener("animationend", handleEnd, { once: true });

  void target.offsetHeight;
  target.classList.add(className);

  if (timeout > 0) {
    setTimeout(() => settle(onFinish), timeout);
  }

  return () => {
    if (!settled) {
      settle(onCancel);
    }
  };
}
