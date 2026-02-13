const MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function getMotionMediaQuery() {
  if (typeof window === "undefined") {
    return null;
  }
  if (typeof window.matchMedia !== "function") {
    return null;
  }
  return window.matchMedia(MOTION_QUERY);
}

export function prefersReducedMotion() {
  const query = getMotionMediaQuery();
  return Boolean(query?.matches);
}

const cachedReduceMotion = prefersReducedMotion();

export function isMotionReduced() {
  return cachedReduceMotion;
}

export function isMotionSafe() {
  return !cachedReduceMotion;
}

export function motionSafeBehavior(behavior = "smooth", reduceMotion = cachedReduceMotion) {
  if (!reduceMotion) {
    return behavior;
  }
  return behavior === "smooth" ? "auto" : behavior;
}

export function withMotionPreference({ safe, reduced }) {
  if (cachedReduceMotion) {
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

const LABEL_FLASH_CLASS = "text-glow-flash";

export function triggerLabelFlash(node) {
  if (!node) return;
  node.classList.remove(LABEL_FLASH_CLASS);
  void node.offsetHeight;
  node.classList.add(LABEL_FLASH_CLASS);
}
