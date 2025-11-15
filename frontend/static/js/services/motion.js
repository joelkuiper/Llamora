import {
  motionSafeBehavior as computeMotionSafeBehavior,
  prefersReducedMotion as detectReducedMotion,
} from "../utils/motion.js";

const reduceMotionPreference = detectReducedMotion();

export function isMotionReduced() {
  return reduceMotionPreference;
}

export function isMotionSafe() {
  return !reduceMotionPreference;
}

export function motionSafeBehavior(behavior = "smooth") {
  return computeMotionSafeBehavior(behavior, reduceMotionPreference);
}

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
  } = options;

  if (reduceMotionPreference) {
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
  target.classList.add(className);

  return () => {
    if (!settled) {
      settle(onCancel);
    }
  };
}

export function withMotionPreference({ safe, reduced }) {
  if (reduceMotionPreference) {
    if (typeof reduced === "function") {
      reduced();
    }
    return false;
  }

  if (typeof safe === "function") {
    safe();
  }
  return true;
}

